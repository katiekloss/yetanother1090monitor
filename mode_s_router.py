#!/usr/bin/env python
import aio_pika
import asyncio
import argparse
import os
import pyModeS as pms
import daemon
import sys

from pidlockfile import PIDLockFile
from setproctitle import setproctitle


async def main(args):
    rabbit = await aio_pika.connect_robust(args.rabbit)

    channel = await rabbit.channel()
    mode_s_exchange = await channel.declare_exchange("mode_s_by_downlink",
                                                     aio_pika.ExchangeType.TOPIC,
                                                     durable = True)
    adsb_exchange = await channel.declare_exchange("adsb",
                                                   aio_pika.ExchangeType.TOPIC,
                                                   durable = True)

    drop_queue = await channel.declare_queue("mode_s_by_downlink_default",
                                        durable = False,
                                        arguments = {"x-max-length": 1, "x-overflow": "drop-head"})

    await drop_queue.bind(mode_s_exchange, "*")

    await channel.set_qos(prefetch_count = 10)

    queue = await channel.declare_queue("mode_s_router",
                                        durable = True)

    await queue.bind("mode_s", "raw")

    async with queue.iterator() as queue_iter:
        async for message in queue_iter:
            async with message.process():
                await route(message, mode_s_exchange, adsb_exchange)

async def route(message, mode_s_exchange, adsb_exchange):
    data = message.body.decode()
    try:
        df = pms.df(data)
    except:
        print(f"Failed to decode: {data}")
        return

    try:
        icao = pms.icao(data)
    except:
        print(f"No ICAO on {df}: {data}")
        return

    tc = pms.adsb.typecode(data)
    if tc == None and 17 <= df <= 18:
        print(f"No typecode ({df}): {data}")
        return

    routed_message = aio_pika.Message(message.body,
                                      headers = {"icao": icao,
                                                 "typecode": tc,
                                                 "downlink": df})
    
    await mode_s_exchange.publish(routed_message, routing_key = str(df))
    if df == 17 or df == 18:
        await adsb_exchange.publish(routed_message, f"icao.{icao}.typecode.{tc}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("-d", "--daemon", action="store_true")
    parser.add_argument("-p", "--pidfile")
    parser.add_argument("-r", "--rabbit", required=True)
    args = parser.parse_args()

    if args.daemon:
        if args.pidfile is None or len(args.pidfile) == 0:
            log.error("-p/--pidfile is required when --daemon is present")
            sys.exit(1)

        with daemon.DaemonContext(pidfile=PIDLockFile(args.pidfile, timeout=2.0)):
            log.info("Starting")
            setproctitle("yetanother1090monitor: mode_s_router")
            asyncio.run(main(args))
    else:
        log.info("Starting")
        asyncio.run(main(args))

