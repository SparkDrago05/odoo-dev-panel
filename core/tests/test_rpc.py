import asyncio
import socket
import unittest

from odoo_dev_panel import rpc


async def _pair(server_handlers):
    a, b = socket.socketpair()
    ra, wa = await asyncio.open_connection(sock=a, limit=rpc.MAX_FRAME)
    rb, wb = await asyncio.open_connection(sock=b, limit=rpc.MAX_FRAME)
    server = rpc.Connection(ra, wa, server_handlers, name="server")
    client = rpc.Connection(rb, wb, {}, name="client")
    server.spawn(server.serve())
    client.spawn(client.serve())
    return server, client


async def echo(params, conn):
    return params


async def fail(params, conn):
    raise rpc.RpcError(rpc.NOT_FOUND, "nope", {"x": 1})


async def crash(params, conn):
    raise ValueError("boom")


class RpcTest(unittest.TestCase):
    def test_many_concurrent_requests(self):
        async def main():
            server, client = await _pair({"echo": echo})
            results = await asyncio.gather(*(client.request("echo", {"n": i}) for i in range(10000)))
            self.assertEqual([r["n"] for r in results], list(range(10000)))
            await client.close()
            await server.close()

        asyncio.run(main())

    def test_large_payload_and_unicode(self):
        async def main():
            server, client = await _pair({"echo": echo})
            payload = {"data": "é✓\r\nContent-Length: 5\r\n\r\n" * 50000}
            self.assertEqual(await client.request("echo", payload), payload)
            await client.close()

        asyncio.run(main())

    def test_errors(self):
        async def main():
            server, client = await _pair({"fail": fail, "crash": crash})
            with self.assertRaises(rpc.RpcError) as ctx:
                await client.request("fail")
            self.assertEqual(ctx.exception.code, rpc.NOT_FOUND)
            self.assertEqual(ctx.exception.data, {"x": 1})
            with self.assertRaises(rpc.RpcError) as ctx:
                await client.request("crash")
            self.assertEqual(ctx.exception.code, rpc.INTERNAL_ERROR)
            with self.assertRaises(rpc.RpcError) as ctx:
                await client.request("missing")
            self.assertEqual(ctx.exception.code, rpc.METHOD_NOT_FOUND)
            await client.close()

        asyncio.run(main())

    def test_pending_requests_fail_on_close(self):
        async def slow(params, conn):
            await asyncio.sleep(10)

        async def main():
            server, client = await _pair({"slow": slow})
            task = asyncio.ensure_future(client.request("slow"))
            await asyncio.sleep(0.05)
            await server.close()
            with self.assertRaises(rpc.ConnectionClosed):
                await task

        asyncio.run(main())


if __name__ == "__main__":
    unittest.main()
