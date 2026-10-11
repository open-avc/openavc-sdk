"""An invented TCP device that uses a normal driver and has no SDK."""
import asyncio


async def main():
    level = 0

    async def connected(reader, writer):
        nonlocal level
        try:
            while line := await reader.readline():
                command = line.decode().strip()
                if command.startswith('LEVEL '):
                    level = int(command[6:])
                elif command != 'POLL':
                    continue
                writer.write(f'LEVEL={level}\r\n'.encode())
                await writer.drain()
        except (ConnectionError, ValueError):
            pass
        finally:
            writer.close()
            await writer.wait_closed()

    server = await asyncio.start_server(connected, '0.0.0.0', 19000)
    print('READY', flush=True)
    async with server:
        await server.serve_forever()


if __name__ == '__main__':
    asyncio.run(main())
