import asyncio
import unittest

from odoo_dev_panel import logs

# Shaped after real Odoo 17 output: werkzeug perf info, a failing request logged twice, a SQL warning.
SAMPLE = """\
Using Python 3.10.21 environment at: venv
2026-10-04 10:00:00,001 4242 INFO ? odoo: Odoo version 17.0
2026-10-04 10:00:00,120 4242 INFO ? odoo.service.server: HTTP service (werkzeug) running on host:8069
2026-10-04 10:00:05,500 4250 INFO client_a werkzeug: 127.0.0.1 - - [04/Oct/2026 10:00:05] "GET /web HTTP/1.1" 200 - 42 0.051 0.210
2026-10-04 10:00:06,000 4250 WARNING client_a odoo.models: sale.order.read() with unknown field 'x_old' (id 17)
2026-10-04 10:00:07,000 4250 ERROR client_a odoo.http: Exception during request handling. - - -
Traceback (most recent call last):
  File "/opt/odoo17/odoo/odoo/http.py", line 2190, in __call__
    response = request._serve_db()
  File "/opt/odoo17/custom/addons/sale_ext/models/sale.py", line 88, in action_confirm
    raise UserError("Missing partner for order %s" % self.name)
odoo.exceptions.UserError: Missing partner for order S00042
2026-10-04 10:00:08,000 4250 WARNING client_a odoo.models: sale.order.read() with unknown field 'x_old' (id 18)
2026-10-04 10:00:09,000 4251 ERROR client_b odoo.http: Exception during request handling.
_Traceback_ (most recent call last):
  File "/home/dev/src/odoo/odoo/http.py", line 2190, in __call__
    response = request._serve_db()
  File "/home/dev/src/custom/addons/sale_ext/models/sale.py", line 88, in action_confirm
    raise UserError("Missing partner for order %s" % self.name)
odoo.exceptions.UserError: Missing partner for order S00099
2026-10-04 10:00:10,000 4242 CRITICAL ? odoo.service.server: Failed to load registry
"""


class ParseTest(unittest.TestCase):
    def test_records_and_continuations(self):
        records = logs.parse(SAMPLE)
        self.assertEqual(records[0].level, None, "output before Odoo logging starts")
        self.assertEqual([r.level for r in records[1:]],
                         ["INFO", "INFO", "INFO", "WARNING", "ERROR", "WARNING", "ERROR", "CRITICAL"])
        req = records[3]
        self.assertTrue(req.message.endswith('"GET /web HTTP/1.1" 200 -'), req.message)
        err = records[5]
        self.assertEqual((err.line, err.pid, err.db, err.logger), (6, 4250, "client_a", "odoo.http"))
        self.assertEqual(err.message, "Exception during request handling.")
        self.assertEqual(len(err.extra), 6)

    def test_traceback_last_frame_and_exception(self):
        tb = logs.parse(SAMPLE)[5].traceback()
        self.assertEqual(tb["type"], "odoo.exceptions.UserError")
        self.assertEqual(tb["text"], "Missing partner for order S00042")
        self.assertEqual((tb["frame"]["line"], tb["frame"]["function"]), (88, "action_confirm"))

    def test_chained_exception_reports_the_last(self):
        text = (
            "2026-10-04 10:00:00,000 1 ERROR db odoo.sql_db: bad query\n"
            "Traceback (most recent call last):\n"
            '  File "/x/odoo/sql_db.py", line 1, in execute\n'
            "psycopg2.errors.UndefinedColumn: column x does not exist\n"
            "\n"
            "During handling of the above exception, another exception occurred:\n"
            "\n"
            "Traceback (most recent call last):\n"
            '  File "/x/odoo/models.py", line 9, in _read\n'
            "ValueError: wrapped\n"
        )
        tb = logs.parse(text)[0].traceback()
        self.assertEqual((tb["type"], tb["frame"]["function"]), ("ValueError", "_read"))

    def test_colour_codes_from_a_pty_are_removed(self):
        text = "2026-10-04 10:00:00,000 1 \x1b[1;31m\x1b[1;49mERROR\x1b[0m db odoo.x: boom\r\n"
        [record] = logs.parse(text)
        self.assertEqual((record.level, record.message), ("ERROR", "boom"))


class AnalyzeTest(unittest.TestCase):
    def test_groups_repeats_across_checkouts_and_dbs(self):
        result = logs.analyze(SAMPLE)
        self.assertEqual(result["counts"], {"DEBUG": 0, "INFO": 3, "WARNING": 2, "ERROR": 2, "CRITICAL": 1})
        self.assertEqual([(g["level"], g["count"]) for g in result["groups"]], [("CRITICAL", 1), ("ERROR", 2), ("WARNING", 2)])
        error = result["groups"][1]
        self.assertEqual(error["title"], "odoo.exceptions.UserError: Missing partner for order S00042")
        self.assertEqual(error["dbs"], ["client_a", "client_b"])
        self.assertEqual((error["first_line"], error["last_line"]), (6, 14))
        self.assertIn("Traceback", error["sample"])
        self.assertEqual(result["first_error_line"], 6)

    def test_different_messages_stay_apart(self):
        text = (
            "2026-10-04 10:00:00,000 1 WARNING db odoo.a: field x missing\n"
            "2026-10-04 10:00:00,000 1 WARNING db odoo.a: access denied\n"
            "2026-10-04 10:00:00,000 1 WARNING db odoo.b: field x missing\n"
        )
        self.assertEqual(len(logs.analyze(text)["groups"]), 3)

    def test_minimum_level(self):
        self.assertEqual([g["level"] for g in logs.analyze(SAMPLE, "ERROR")["groups"]], ["CRITICAL", "ERROR"])


class LevelFilterTest(unittest.TestCase):
    def test_keeps_tracebacks_and_works_across_chunks(self):
        f = logs.LevelFilter("ERROR")
        out = "".join(f.feed(SAMPLE[i:i + 7]) for i in range(0, len(SAMPLE), 7)) + f.flush()
        self.assertTrue(out.startswith("Using Python"), "output before the first record is kept")
        self.assertNotIn(" INFO ", out)
        self.assertNotIn(" WARNING ", out)
        self.assertEqual(out.count("(most recent call last)"), 2)
        self.assertIn("CRITICAL", out)

    def test_drop_other_output(self):
        f = logs.LevelFilter("WARNING", keep_other=False)
        self.assertNotIn("Using Python", f.feed(SAMPLE) + f.flush())

    def test_unknown_level(self):
        with self.assertRaises(ValueError):
            logs.LevelFilter("LOUD")


class ReadTailTest(unittest.TestCase):
    def test_reads_tail_from_a_whole_line(self):
        data = "".join(f"line {i}\n" for i in range(1000))

        class Conn:
            async def request(self, method, params):
                if method == "session.get":
                    return {"log_size": len(data)}
                start = params["offset"]
                chunk = data[start:start + min(params["limit"], 100)]
                return {"data": chunk, "offset": start + len(chunk)}

        text, start = asyncio.run(logs.read_tail(Conn(), "s1", max_bytes=500))
        self.assertEqual(start, len(data) - 500)
        self.assertTrue(text.startswith("line "), text[:20])
        self.assertTrue(text.endswith("line 999\n"))
        full, start = asyncio.run(logs.read_tail(Conn(), "s1"))
        self.assertEqual((full, start), (data, 0))


if __name__ == "__main__":
    unittest.main()
