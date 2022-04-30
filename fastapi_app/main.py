import logging
import os
import random
import time
from typing import Optional

import httpx
import uvicorn
from fastapi import FastAPI, Response
from opentelemetry import trace
from opentelemetry.exporter.jaeger.proto.grpc import \
    JaegerExporter as GrpcJaegerExporter
from opentelemetry.exporter.jaeger.thrift import \
    JaegerExporter as ThriftJaegerExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.logging import LoggingInstrumentor
from opentelemetry.propagate import inject
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from starlette.types import ASGIApp

APP_NAME = os.environ.get("APP_NAME", "app")
EXPOSE_PORT = os.environ.get("EXPOSE_PORT", 8000)

# grpc, thrift-collector,  thrift-agent
MODE = os.environ.get("MODE", "grpc")

# with grpc through jaeger collector
COLLECTOR_ENDPOINT_GRPC_ENDPOINT = os.environ.get(
    "COLLECTOR_ENDPOINT_GRPC_ENDPOINT", "jaeger-collector:14250")

# with thrift through jaeger collector
COLLECTOR_THRIFT_URL = os.environ.get(
    "COLLECTOR_THRIFT_URL", "http://jaeger-collector:14268")

# with thrift through jaeger agent
AGENT_HOST_NAME = os.environ.get("AGENT_HOST_NAME", "jaeger-agent")
AGENT_PORT = int(os.environ.get("AGENT_PORT", 6831))

TARGET_ONE_HOST = os.environ.get("TARGET_ONE_HOST", "app-b")
TARGET_TWO_HOST = os.environ.get("TARGET_TWO_HOST", "app-c")

app = FastAPI()


def setting_jaeger(app: ASGIApp, app_name: str, log_correlation: bool = True) -> None:
    # Setting jaeger
    # set the service name to show in traces
    resource = Resource.create(attributes={
        "service.name": app_name
    })

    # set the tracer provider
    tracer = TracerProvider(resource=resource)
    trace.set_tracer_provider(tracer)

    # use different mode to push span according environment variable MODE
    if MODE == "thrift-collector":
        tracer.add_span_processor(BatchSpanProcessor(ThriftJaegerExporter(
            collector_endpoint=f'{COLLECTOR_THRIFT_URL}/api/traces?format=jaeger.thrift',
        )))
    elif MODE == "thrift-agent":
        tracer.add_span_processor(BatchSpanProcessor(ThriftJaegerExporter(
            agent_host_name=AGENT_HOST_NAME,
            agent_port=AGENT_PORT,
        )))
    else:
        # grpc (default)
        tracer.add_span_processor(BatchSpanProcessor(GrpcJaegerExporter(
            collector_endpoint=COLLECTOR_ENDPOINT_GRPC_ENDPOINT, insecure=True)))

    # override logger format which with trace id and span id
    if log_correlation:
        LoggingInstrumentor().instrument(set_logging_format=True)

    FastAPIInstrumentor.instrument_app(app, tracer_provider=tracer)


# Setting jaeger exporter
setting_jaeger(app, APP_NAME)


@app.get("/")
async def read_root():
    logging.error("Hello World")
    return {"Hello": "World"}


@app.get("/items/{item_id}")
async def read_item(item_id: int, q: Optional[str] = None):
    logging.error("items")
    return {"item_id": item_id, "q": q}


@app.get("/io_task")
async def io_task():
    time.sleep(1)
    logging.error("io task")
    return "IO bound task finish!"


@app.get("/cpu_task")
async def cpu_task():
    for i in range(1000):
        n = i*i*i
    logging.error("cpu task")
    return "CPU bound task finish!"


@app.get("/random_status")
async def random_status(response: Response):
    response.status_code = random.choice([200, 200, 300, 400, 500])
    logging.error("random status")
    return {"path": "/random_status"}


@app.get("/random_sleep")
async def random_sleep(response: Response):
    time.sleep(random.randint(0, 5))
    logging.error("random sleep")
    return {"path": "/random_sleep"}


@app.get("/error_test")
async def random_sleep(response: Response):
    logging.error("got error!!!!")
    raise ValueError("value error")


@app.get("/chain")
async def chain(response: Response):

    headers = {}
    inject(headers)  # inject trace info to header
    logging.critical(headers)

    async with httpx.AsyncClient() as client:
        await client.get(f"http://localhost:8000/", headers=headers,)
    async with httpx.AsyncClient() as client:
        await client.get(f"http://{TARGET_ONE_HOST}:8000/io_task", headers=headers,)
    async with httpx.AsyncClient() as client:
        await client.get(f"http://{TARGET_TWO_HOST}:8000/cpu_task", headers=headers,)
    logging.info("Chain Finished")
    return {"path": "/chain"}

if __name__ == "__main__":
    # update uvicorn access logger format
    log_config = uvicorn.config.LOGGING_CONFIG
    log_config["formatters"]["access"]["fmt"] = "%(asctime)s %(levelname)s [%(name)s] [%(filename)s:%(lineno)d] [trace_id=%(otelTraceID)s span_id=%(otelSpanID)s resource.service.name=%(otelServiceName)s] - %(message)s"
    uvicorn.run(app, host="0.0.0.0", port=EXPOSE_PORT, log_config=log_config)
