import hashlib
import struct
import os
from bisect import bisect_left

class Server(object):
    def __init__(self, server_num=5, vnode_num=100):
        self._server_num = server_num
        self._vnode_in_ring = []
        self._vnode_2_server = []
        self._vnode_num = vnode_num

        for i in range(self._server_num):
            dir_path = '/tmp/server%s' % i
            if not os.path.isdir(dir_path):
                os.mkdir('/tmp/server%s' % i)

        vstep = (1<<32) / self._vnode_num
        step = self._vnode_num / self._server_num
        for i in range(self._vnode_num):
            self._vnode_in_ring.append(vstep*(i+1))
            self._vnode_2_server.append(i%self._server_num)

    def _md5_hash(self, content):
        md5obj = hashlib.md5()
        md5obj.update(content)
        md5_value = md5obj.digest()
        hash_value = struct.unpack_from('>I', md5_value)[0]
        return hash_value % (1<<32)

    def _hash_object(self, file_path):
        with open(file_path, 'rb') as f:
            return self._md5_hash(f.read())

    def _get_server(self, file_path):
        hash_value = self._hash_object(file_path)
        viter = bisect_left(self._vnode_in_ring, hash_value) % \
                len(self._vnode_in_ring)
        server_id = self._vnode_2_server[viter]
        return server_id

    def store(self, file_path):
        if os.path.isfile(file_path):
            ith_server = self._get_server(file_path)
            os.popen('cp -rf %s /tmp/server%s' % (file_path, ith_server))
        else:
            print 'We need to store files!'
