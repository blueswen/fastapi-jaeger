# FastAPI Tracing with Jaeger through OpenTelemetry

Trace FastAPI with [Jaeger](https://www.jaegertracing.io/) through [OpenTelemetry Python API and SDK](https://github.com/open-telemetry/opentelemetry-python).

The span from the application could be collected with [Jaeger Collector](https://www.jaegertracing.io/docs/1.33/architecture/#collector) or [Jaeger Agent](https://www.jaegertracing.io/docs/1.33/architecture/#agent):

![Demo Project Architecture](./images/demo-arch.jpg)

There are three ways to push span:

- A: Push span to the agent with Thrift format over UDP (Port: 6831)
- B: Push span to collector with Thrift format over HTTP (Port: 14268)
- C: Push span to collector over gRPC (Port: 14250)

In this architecture, Jaeger Collector is responsible for collecting span and writing span to DB, then Jaeger Query queries data from DB.

## Quick Start

1. Build application image and start all services with docker-compose

   ```bash
   docker-compose build
   docker-compose up -d
   ```

   It may take some time for DB(Cassandra) to initialize. You can run `docker-compose ps` to check the `jaeger-query` status is running when DB is ready.

2. Send requests with `curl` to the FastAPI application

   ```bash
   curl http://localhost:8000/chain
   ```

3. Check on Jaeger UI [http://localhost:16686/](http://localhost:16686/)

   Jaeger UI screenshot:

   ![Jaeger UI](./images/jaeger-ui.png)

   ![Jaeger UI Trace](./images/jaeger-ui-trace.png)

## Detail

### FastAPI Application

For a more complex scenario, we use three FastAPI applications with the same code in this demo. There is a cross-service action in `/chain` endpoint, which provides a good example of how to use OpenTelemetry SDK process span and how Jaeger Query presents trace information.

#### Traces and Logs

Utilize [OpenTelemetry Python SDK](https://github.com/open-telemetry/opentelemetry-python) to send span to Jaeger. Each request span contains other child spans when using OpenTelemetry instrumentation. The reason is that instrumentation will catch each internal asgi interaction ([opentelemetry-python-contrib issue #831](https://github.com/open-telemetry/opentelemetry-python-contrib/issues/831#issuecomment-1005163018)). If you want to get rid of the internal spans, there is a [workaround](https://github.com/open-telemetry/opentelemetry-python-contrib/issues/831#issuecomment-1116225314) in the same issue #831 by using a new OpenTelemetry middleware with two overridden methods of span processing.

Utilize [OpenTelemetry Logging Instrumentation](https://opentelemetry-python-contrib.readthedocs.io/en/latest/instrumentation/logging/logging.html) override logger format which with trace id and span id.

```py
# fastapi_app/main.py

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
```

#### Span Inject

If we want other services to use the same Trace ID, we have to use `inject` function to add current span information to the header. Because OpenTelemetry FastAPI instrumentation only takes care of the asgi app's request and response, it does not affect any other modules or actions like sending HTTP requests to other servers or function calls.

```py
# fastapi_app/main.py

from opentelemetry.propagate import inject

@app.get("/chain")
async def chain(response: Response):

    headers = {}
    inject(headers)  # inject trace info to header

    async with httpx.AsyncClient() as client:
        await client.get(f"http://localhost:8000/", headers=headers,)
    async with httpx.AsyncClient() as client:
        await client.get(f"http://{TARGET_ONE_HOST}:8000/io_task", headers=headers,)
    async with httpx.AsyncClient() as client:
        await client.get(f"http://{TARGET_TWO_HOST}:8000/cpu_task", headers=headers,)

    return {"path": "/chain"}
```

### Jaeger

There is an [all-in-one](https://www.jaegertracing.io/docs/1.33/getting-started/#all-in-one) Jaeger for quick testing, but in production running Jaeger backend components as a scalable distributed system is the most common method, as illustrated below.

![Jaeger Architecture](./images/jaeger-architecture-v1.png)

Image Source: [Jaeger](https://www.jaegertracing.io/docs/1.33/architecture/#components)

We use the [docker compose example](https://github.com/jaegertracing/jaeger/blob/main/docker-compose/jaeger-docker-compose.yml) from Jaeger official repository as this demo's Jaeger backend.

Check more details on [Jaeger docs about architecture](https://www.jaegertracing.io/docs/1.33/architecture/).

#### Jaeger Agent

The Jaeger agent is a network daemon that listens for spans sent over UDP, which it batches and sends to the collector.

```yaml
# docker-compose.yaml
services:
  jaeger-agent:
    image: jaegertracing/jaeger-agent
    command:
      - "--reporter.grpc.host-port=jaeger-collector:14250" # collector grpc host and port
    ports:
      - "5775:5775/udp" # accept ziplin.thrift in compact (deprecated) 
      - "6831:6831/udp" # accept jaeger.thrift in compact
      - "6832:6832/udp" # accept jaeger.thrift in binary used by Node.js Jaeger client
      - "5778:5778" # server configs
    restart: on-failure
    depends_on:
      - jaeger-collector
```

Check more details on Jaeger docs [Deployment about Agent](https://www.jaegertracing.io/docs/1.33/deployment/#agent) and [CLI flags about Agent](https://www.jaegertracing.io/docs/1.33/cli/#jaeger-agent).

#### Jaeger Collector

The Jaeger collector receives traces from Jaeger agents and runs them through a processing pipeline.

```yaml
# docker-compose.yaml
services:
  jaeger-collector:
    image: jaegertracing/jaeger-collector
    command: 
      - "--cassandra.keyspace=jaeger_v1_dc1" # cassandra keyspace
      - "--cassandra.servers=cassandra" # cassandra server host
      - "--collector.zipkin.host-port=9411" # port accept Zipkin spans
      - "--sampling.initial-sampling-probability=.5" # adaptive sampling options
      - "--sampling.target-samples-per-second=.01" # adaptive sampling options
    environment: 
      - SAMPLING_CONFIG_TYPE=adaptive # with adaptive sampling strategy
    ports:
      - "14269:14269" # admin port
      - "14268:14268" # accept span in jaeger.thrift format over http
      - "14250" # accept span in model.proto format over gRPC
      - "9411:9411" # accept Zipkin spans
    restart: on-failure
    depends_on:
      - cassandra-schema
```

Check more details on Jaeger docs [Deployment about Collector](https://www.jaegertracing.io/docs/1.33/deployment/#collector), [CLI flags about Collector](https://www.jaegertracing.io/docs/1.33/cli/#jaeger-collector), and [Sampling](https://www.jaegertracing.io/docs/1.33/sampling/).

#### Storage

All traces collected by Jaeger Collector will be validated, indexed, and then stored in storage. Jaeger supports multiple span storage backends:

1. Cassandra 3.4+
2. Elasticsearch 5.x, 6.x, 7.x
3. Kafka
4. memory storage
5. Storage plugin

In this demo, we use Cassandra as the storage backend.

```yaml
# docker-compose.yaml
services:
  # Cassandra instance container
  cassandra:
    image: cassandra:4.0

  # initialize Cassandra
  cassandra-schema:
    image: jaegertracing/jaeger-cassandra-schema
    depends_on:
      - cassandra
```

Check more details on Jaeger docs [Deployment about Span Storage Backends](https://www.jaegertracing.io/docs/1.33/deployment/#span-storage-backends).

#### Jaeger Query

The Jaeger Query is a service that retrieves traces from storage and hosts a UI to display them.

```yaml
# docker-compose.yaml
services:
  jaeger-query:
    image: jaegertracing/jaeger-query
    command: 
      - "--cassandra.keyspace=jaeger_v1_dc1" # cassandra keyspace
      - "--cassandra.servers=cassandra" # cassandra server host
    ports:
      - "16686:16686" # Jaeger UI and api port
      - "16687" # admin port
    restart: on-failure
    depends_on:
      - cassandra-schema
```

Check more details on Jaeger docs [Deployment about Query Service & UI](https://www.jaegertracing.io/docs/1.33/deployment/#query-service--ui).

## With Grafana and Loki

Only viewing the trace information on Jaeger UI may not be good enough. How about also tracing with logs at the same time? [Grafana](https://github.com/grafana/grafana) started supporting [Jaeger data source](https://grafana.com/docs/grafana/latest/datasources/jaeger/) since Grafana 7.4+, and also provides a good log aggregation system [Loki](https://github.com/grafana/loki/). Grafana provides a great user experience tracing information across logs and traces.

![Demo Project Architecture with Loki and Grafana](./images/demo-arch-grafana.jpg)

### Quick Start

1. Install [Loki Docker Driver](https://grafana.com/docs/loki/latest/clients/docker-driver/)

   ```bash
   docker plugin install grafana/loki-docker-driver:latest --alias loki --grant-all-permissions
   ```

2. Build application image and start all services with docker-compose

   ```bash
   docker-compose build
   docker-compose up -d
   ```

   It may take some time for DB(Cassandra) to initialize.

3. Send requests with `curl` to the FastAPI application

   ```bash
   curl http://localhost:8000/chain
   ```

4. Explore the collected span on Grafana [http://localhost:3000/](http://localhost:3000/)

   Grafana Explore screenshot:

   ![Explore Jaeger on Grafana](./images/explore-jaeger.png)

### Explore with Grafana

#### Traces to Logs

Get Trace ID and tags (here is `compose.service` mapping to `compose_service`) defined in Jaeger data source from span, then query with Loki.

![Traces to Logs](./images/traces-to-logs.png)

#### Logs to Traces

Get Trace ID pared from log (regex defined in Loki data source), then query in Jaeger.

![Logs to Traces](./images/logs-to-traces.png)

### Detail

#### Jaeger - Traces

Receives spans from applications.

##### Grafana Data Source

[Trace to logs](https://grafana.com/docs/grafana/latest/datasources/jaeger/#trace-to-logs) setting:

1. Data source: target log source
2. Tags: key of tags or process level attributes from the trace, which will be log query criteria if the key exists in the trace
3. Map tag names: Convert existing key of tags or process level attributes from trace to another key, then used as log query criterial. Use this feature when the values of the trace tag and log label are identical but the keys are different.

Grafana data source setting example:

![Data Source of Jaeger: Trace to logs](./images/jaeger-trace-to-logs.png)

Grafana data sources config example:

```yaml
name: Jaeger
type: jaeger
typeName: Jaeger
access: proxy
url: http://jaeger-query:16686
user: ''
database: ''
basicAuth: false
isDefault: false
jsonData:
  nodeGraph:
    enabled: true
  tracesToLogs:
    datasourceUid: loki
    filterBySpanID: false
    filterByTraceID: true
    mapTagNamesEnabled: true
    mappedTags:
      - key: service.name
        value: compose_service
```

#### Loki - Logs

Collects logs with Loki Docker Driver from applications.

##### Loki Docker Driver

1. Use [YAML anchor and alias](https://support.atlassian.com/bitbucket-cloud/docs/yaml-anchors/) feature to set logging options for each service.
2. Set [Loki Docker Driver options](https://grafana.com/docs/loki/latest/clients/docker-driver/configuration/)
   1. loki-url: loki service endpoint
   2. loki-pipeline-stages: processes multiline log from FastAPI application with multiline and regex stages ([reference](https://grafana.com/docs/loki/latest/clients/promtail/stages/multiline/))

```yaml
x-logging: &default-logging # anchor(&): 'default-logging' for defines a chunk of configuration
  driver: loki
  options:
    loki-url: 'http://localhost:3100/api/prom/push'
    loki-pipeline-stages: |
      - multiline:
          firstline: '^\d{4}-\d{2}-\d{2} \d{1,2}:\d{2}:\d{2}'
          max_wait_time: 3s
      - regex:
          expression: '^(?P<time>\d{4}-\d{2}-\d{2} \d{1,2}:\d{2}:\d{2},d{3}) (?P<message>(?s:.*))$$'
# Use $$ (double-dollar sign) when your configuration needs a literal dollar sign.

version: "3.4"

services:
   foo:
      image: foo
      logging: *default-logging # alias(*): refer to 'default-logging' chunk 
```

##### Grafana Data Source

Add a TraceID derived field to extract the trace id and create a Jaeger link from the trace id.

Grafana data source setting example:

![Data Source of Loki: Derived fields](./images/loki-derive-filed.png)

Grafana data source config example:

```yaml
name: Loki
type: loki
typeName: Loki
access: proxy
url: http://loki:3100
password: ''
user: ''
database: ''
basicAuth: false
isDefault: false
jsonData:
derivedFields:
   - datasourceUid: jaeger
      matcherRegex: (?:trace_id)=(\w+)
      name: TraceID
      url: $${__value.raw}
      # Use $$ (double-dollar sign) when your configuration needs a literal dollar sign.
readOnly: false
editable: true
```

#### Grafana

1. Add Jaeger, and Loki to the data source with config file ```etc/grafana/datasource.yml```.

```yaml
# grafana in docker-compose.yaml
grafana:
   image: grafana/grafana:9.1.7
   volumes:
      - ./etc/grafana/:/etc/grafana/provisioning/datasources # data sources
```

# Reference

1. [Jaeger](https://www.jaegertracing.io/)
2. [Official Jaeger docker compose example](https://github.com/jaegertracing/jaeger/blob/main/docker-compose/jaeger-docker-compose.yml)
