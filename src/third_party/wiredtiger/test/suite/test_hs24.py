#!/usr/bin/env python
#
# Public Domain 2014-present MongoDB, Inc.
# Public Domain 2008-2014 WiredTiger, Inc.
#
# This is free and unencumbered software released into the public domain.
#
# Anyone is free to copy, modify, publish, use, compile, sell, or
# distribute this software, either in source code form or as a compiled
# binary, for any purpose, commercial or non-commercial, and by any
# means.
#
# In jurisdictions that recognize copyright laws, the author or authors
# of this software dedicate any and all copyright interest in the
# software to the public domain. We make this dedication for the benefit
# of the public at large and to the detriment of our heirs and
# successors. We intend this dedication to be an overt act of
# relinquishment in perpetuity of all present and future rights to this
# software under copyright law.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND,
# EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF
# MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT.
# IN NO EVENT SHALL THE AUTHORS BE LIABLE FOR ANY CLAIM, DAMAGES OR
# OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE,
# ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR
# OTHER DEALINGS IN THE SOFTWARE.

import wttest, threading, wiredtiger
from helper import simulate_crash_restart
from wtscenario import make_scenarios

# test_hs24.py
# Test that out of order timestamp fix racing with checkpointing the history store doesn't create inconsistent checkpoint.
class test_hs24(wttest.WiredTigerTestCase):
    conn_config = 'cache_size=50MB,timing_stress_for_test=(history_store_checkpoint_delay)'
    session_config = 'isolation=snapshot'
    uri = 'table:test_hs24'
    numrows = 2000

    key_format_values = [
        ('column', dict(key_format='r')),
        ('integer_row', dict(key_format='i')),
    ]

    scenarios = make_scenarios(key_format_values)

    value1 = 'a' * 500
    value2 = 'b' * 500
    value3 = 'c' * 500
    value4 = 'd' * 500
    def test_zero_ts(self):
        self.session.create(self.uri, 'key_format={},value_format=S'. format(self.key_format))
        self.conn.set_timestamp('oldest_timestamp=' + self.timestamp_str(1))
        cursor = self.session.open_cursor(self.uri)
        for i in range(1, self.numrows + 1):
            self.session.begin_transaction()
            cursor[i] = self.value1
            self.session.commit_transaction('commit_timestamp=' + self.timestamp_str(4))
            self.session.begin_transaction()
            cursor[i] = self.value2
            self.session.commit_transaction('commit_timestamp=' + self.timestamp_str(5))
        cursor.close()
        self.conn.set_timestamp('stable_timestamp=' + self.timestamp_str(5))
        thread = threading.Thread(target=self.zero_ts_deletes)
        thread.start()
        self.session.checkpoint()
        thread.join()
        simulate_crash_restart(self, '.', "RESTART")
        cursor = self.session.open_cursor(self.uri)
        session2 = self.conn.open_session(None)
        cursor2 = session2.open_cursor(self.uri)
        self.session.begin_transaction('read_timestamp=' + self.timestamp_str(5))
        session2.begin_transaction('read_timestamp=' + self.timestamp_str(4))
        # Check the data store and the history store content is consistent.
        # If we have a value in the data store, we should see the older
        # version in the history store as well.
        newer_data_visible = False
        for i in range(1, self.numrows + 1):
            cursor.set_key(i)
            cursor2.set_key(i)
            ret = cursor.search()
            ret2 = cursor2.search()
            if not newer_data_visible:
                newer_data_visible = ret != wiredtiger.WT_NOTFOUND
            if newer_data_visible:
                self.assertEquals(cursor.get_value(), self.value2)
                self.assertEquals(cursor2.get_value(), self.value1)
            else:
                self.assertEquals(ret2, wiredtiger.WT_NOTFOUND)
        session2.rollback_transaction()
        self.session.rollback_transaction()

    def zero_ts_deletes(self):
        session = self.setUpSessionOpen(self.conn)
        cursor = session.open_cursor(self.uri)
        for i in range(1, self.numrows + 1):
            session.begin_transaction()
            cursor.set_key(i)
            cursor.remove()
            session.commit_transaction()
        cursor.close()
        session.close()

    def test_zero_commit(self):
        self.session.create(self.uri, 'key_format={},value_format=S'.format(self.key_format))
        self.conn.set_timestamp('oldest_timestamp=' + self.timestamp_str(1))
        cursor = self.session.open_cursor(self.uri)
        for i in range(1, self.numrows + 1):
            self.session.begin_transaction()
            cursor[i] = self.value1
            self.session.commit_transaction('commit_timestamp=' + self.timestamp_str(4))
            self.session.begin_transaction()
            cursor[i] = self.value2
            self.session.commit_transaction('commit_timestamp=' + self.timestamp_str(5))
        cursor.close()
        self.conn.set_timestamp('stable_timestamp=' + self.timestamp_str(4))
        thread = threading.Thread(target=self.zero_ts_commits)
        thread.start()
        self.session.checkpoint()
        thread.join()
        simulate_crash_restart(self, '.', "RESTART")
        cursor = self.session.open_cursor(self.uri)
        self.session.begin_transaction('read_timestamp=' + self.timestamp_str(4))
        # Check we can only see the version committed by the zero timestamp
        # commit thread before the checkpoint starts or value1.
        newer_data_visible = False
        for i in range(1, self.numrows + 1):
            value = cursor[i]
            if not newer_data_visible:
                newer_data_visible = value != self.value3
            if newer_data_visible:
                self.assertEquals(value, self.value1)
            else:
                self.assertEquals(value, self.value3)
        self.session.rollback_transaction()

    def zero_ts_commits(self):
        session = self.setUpSessionOpen(self.conn)
        cursor = session.open_cursor(self.uri)
        for i in range(1, self.numrows + 1):
            session.begin_transaction()
            cursor[i] = self.value3
            session.commit_transaction()
        cursor.close()
        session.close()

    def test_out_of_order_ts(self):
        self.session.create(self.uri, 'key_format={},value_format=S'.format(self.key_format))
        self.conn.set_timestamp('oldest_timestamp=' + self.timestamp_str(1))
        cursor = self.session.open_cursor(self.uri)
        for i in range(1, self.numrows + 1):
            self.session.begin_transaction()
            cursor[i] = self.value1
            self.session.commit_transaction('commit_timestamp=' + self.timestamp_str(4))
            self.session.begin_transaction()
            cursor[i] = self.value2
            self.session.commit_transaction('commit_timestamp=' + self.timestamp_str(5))
        self.conn.set_timestamp('stable_timestamp=' + self.timestamp_str(4))
        for i in range(1, self.numrows + 1):
            self.session.begin_transaction()
            cursor[i] = self.value3
            self.session.commit_transaction('commit_timestamp=' + self.timestamp_str(6))
        cursor.close()
        thread = threading.Thread(target=self.out_of_order_ts_commits)
        thread.start()
        self.session.checkpoint()
        thread.join()
        simulate_crash_restart(self, '.', "RESTART")
        cursor = self.session.open_cursor(self.uri)
        self.session.begin_transaction('read_timestamp=' + self.timestamp_str(4))
        # Check we can only see the version at timestamp 4, it's either
        # committed by the out of order timestamp commit thread before the
        # checkpoint starts or value1.
        newer_data_visible = False
        for i in range(1, self.numrows + 1):
            value = cursor[i]
            if not newer_data_visible:
                newer_data_visible = value != self.value4
            if newer_data_visible:
                self.assertEquals(value, self.value1)
            else:
                self.assertEquals(value, self.value4)
        self.session.rollback_transaction()

    def out_of_order_ts_commits(self):
        session = self.setUpSessionOpen(self.conn)
        cursor = session.open_cursor(self.uri)
        for i in range(1, self.numrows + 1):
            session.begin_transaction()
            cursor[i] = self.value4
            session.commit_transaction('commit_timestamp=' + self.timestamp_str(4))
        cursor.close()
        session.close()
