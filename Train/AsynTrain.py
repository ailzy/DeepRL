from multiprocessing import Process
import random
import sys
from select import select
import tensorflow as tf
import zmq
from time import time


def func_train_process(_create_agent_func, _c2s_port, _s2c_port,
                       _index, _step_update_func):
    random.seed()
    agent = _create_agent_func()
    context = zmq.Context()
    c2s_socket = context.socket(zmq.PUSH)
    c2s_socket.connect('tcp://127.0.0.1:%s' % _c2s_port)
    s2c_socket = context.socket(zmq.PULL)
    s2c_socket.connect('tcp://127.0.0.1:%s' % _s2c_port)

    def update_params():
        c2s_socket.send_pyobj(['params', _index])
        fetch_data = s2c_socket.recv_pyobj()
        for k, v in zip(fetch_data.keys(), fetch_data.values()):
            if k == 'v_func':
                agent.setVFunc(v)
            elif k == 'q_func':
                agent.setQFunc(v)
            elif k == 'p_func':
                agent.setPFunc(v)
            elif k == 'target_v_func':
                agent.setTargetVFunc(v)
            elif k == 'target_q_func':
                agent.setTargetQFunc(v)
            elif k == 'target_p_func':
                agent.setTargetPFunc(v)
            else:
                raise Exception()

    def upload_grads():
        push_data = {}
        if agent.v_vars:
            push_data['v_func'] = agent.v_grads_data
        if agent.q_vars:
            push_data['q_func'] = agent.q_grads_data
        if agent.p_vars:
            push_data['p_func'] = agent.p_grads_data
        c2s_socket.send_pyobj(['grads', _index, push_data])
        s2c_socket.recv()

    update_params()

    while True:
        agent.startNewGame()
        step_local = 0
        while agent.step():
            c2s_socket.send_pyobj(['step', _index])
            s2c_socket.recv()
            step_local += 1
            if step_local % _step_update_func == 0:
                agent.train()
                upload_grads()
                update_params()
        agent.train()
        upload_grads()
        update_params()


class AsynTrain(object):

    def __init__(self, _create_agent_func, _process_num=8,
                 _step_update_func=5,
                 _step_update_target=1e3,
                 _step_save=1e6,
                 _v_opt=None, _q_opt=None, _p_opt=None):
        context = zmq.Context()
        self.c2s_socket = context.socket(zmq.PULL)
        c2s_port = self.c2s_socket.bind_to_random_port('tcp://127.0.0.1')
        self.s2c_socket_list = [context.socket(zmq.PUSH)
                                for _ in range(_process_num)]
        s2c_port_list = [s.bind_to_random_port('tcp://127.0.0.1')
                         for s in self.s2c_socket_list]

        self.process_list = [
            Process(
                target=func_train_process,
                args=(_create_agent_func, c2s_port, s2c_port_list[i],
                      i, _step_update_func))
            for i in range(_process_num)
        ]
        for process in self.process_list:
            process.start()

        self.agent = _create_agent_func()
        if _v_opt is not None:
            self.agent.createVOpt(_v_opt)
        if _q_opt is not None:
            self.agent.createQOpt(_q_opt)
        if _p_opt is not None:
            self.agent.createPOpt(_p_opt)

        self.agent.sess.run(tf.initialize_all_variables())

        self.step_total = 0
        self.step_update_target = _step_update_target
        self.step_save = _step_save

    def run(self):
        while True:
            fetch_data = self.c2s_socket.recv_pyobj()
            cmd = fetch_data[0]
            index = fetch_data[1]
            if cmd == 'step':
                # send ack
                self.s2c_socket_list[index].send('ack')
                self.step_total += 1
                if self.step_total % self.step_update_target == 0:
                    # if update target
                    self.agent.updateTargetFunc()
                if self.step_total % self.step_save == 0:
                    # if save model
                    self.agent.save("", self.step_total)
            elif cmd == 'params':
                # request params
                push_data = {}
                if self.agent.v_vars:
                    push_data['v_func'] = self.agent.getVFunc()
                if self.agent.q_vars:
                    push_data['q_func'] = self.agent.getQFunc()
                if self.agent.p_vars:
                    push_data['p_func'] = self.agent.getPFunc()
                if self.agent.target_v_vars:
                    push_data['target_v_func'] = self.agent.getTargetVFunc()
                if self.agent.target_q_vars:
                    push_data['target_q_func'] = self.agent.getTargetQFunc()
                if self.agent.target_p_vars:
                    push_data['target_p_func'] = self.agent.getTargetPFunc()
                self.s2c_socket_list[index].send_pyobj(push_data)
            elif cmd == 'grads':
                self.s2c_socket_list[index].send('ack')
                # get grads and update model
                fetch_data = fetch_data[2]
                for k, v in zip(fetch_data.keys(), fetch_data.values()):
                    if k == 'v_func':
                        self.agent.v_grads_data = v
                    if k == 'q_func':
                        self.agent.q_grads_data = v
                    if k == 'p_func':
                        self.agent.p_grads_data = v
                self.agent.update()
            else:
                raise Exception()

            # cmd
            rlist, _, _ = select([sys.stdin], [], [], 0.001)
            if rlist:
                print '[[[ interrupted ]]]'
                s = sys.stdin.readline().strip()
                while True:
                    print '[[[ Please input (save, quit, ...) ]]]'
                    s = sys.stdin.readline().strip()
                    if s == 'save':
                        self.agent.save("", self.step_total)
                    elif s == 'quit':
                        break
                    else:
                        print '[[[ unknow cmd... ]]]'
                        pass
            else:
                pass
