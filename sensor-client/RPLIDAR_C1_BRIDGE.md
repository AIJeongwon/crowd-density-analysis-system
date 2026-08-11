# RPLIDAR C1 C++ bridge

`rplidar_c1_bridge.cpp` uses the official SLAMTEC SDK to read complete C1 scans.
It writes newline-delimited JSON (JSONL/NDJSON) to standard output and writes all
diagnostics to standard error. A Python parent process can therefore read one
record at a time from `subprocess.Popen(..., stdout=PIPE, text=True)`.

This is an internal raw-scan IPC contract, not a backend payload.
`lidar_sensor_thread.LidarSensorWorker` launches the bridge, consumes each JSONL scan,
and puts it in the Raspberry Pi's local LiDAR queue for fusion. Raw points are
not sent to the backend and the bridge output should not be piped to a network
client.

## Build

Build the official SDK first, or let the bridge Makefile build it as a
dependency. SDK 2.1.0 or newer is required for C1 support.

```bash
git clone https://github.com/Slamtec/rplidar_sdk.git
make -C sensor-client -f Makefile.rplidar-c1 \
  RPLIDAR_SDK_DIR="$PWD/rplidar_sdk"
```

The executable is created at
`sensor-client/build/rplidar_c1_bridge`. Build it natively on the Raspberry Pi;
an x86 SDK archive cannot be linked into an ARM executable.

## Run

The C1 development kit normally appears as `/dev/ttyUSB0` or a stable
`/dev/serial/by-id/...` path. The bridge defaults to the C1 UART rate of 460800
baud and the `Standard` SDK scan mode.

```bash
sensor-client/build/rplidar_c1_bridge --port /dev/ttyUSB0
```

Use `--help` to see all options. The process handles `SIGINT` and `SIGTERM`; an
in-progress SDK read can delay shutdown by up to `--timeout-ms`.

## Output contract

The first stdout line is a readiness record. It includes the device identity,
SDK version, and the scan mode actually selected by the SDK:

```json
{"type":"ready","timestamp_unix_ms":1786089600000,"port":"/dev/ttyUSB0","baudrate":460800,"device":{"model":0,"firmware":"1.0","hardware":1,"serial":"..."},"sdk_version":"2.1.0","scan_mode":{"id":0,"name":"Standard","us_per_sample":200,"max_distance_m":12,"answer_type":129}}
```

Each subsequent stdout line contains one complete, angle-sorted scan:

```json
{"type":"scan","sequence":0,"timestamp_unix_ms":1786089600100,"monotonic_us":123456789,"scan_mode":"Standard","scan_hz":10,"raw_point_count":500,"point_count":498,"points":[[0.703125,1532.25,47],[1.40625,1528.5,43]]}
```

Each point is `[angle_deg, distance_mm, quality_raw]`. Zero-distance SDK nodes
are omitted. `raw_point_count` is the SDK node count before filtering, while
`point_count` is the number of emitted points. `scan_hz` is `null` if the SDK
does not provide a usable per-sample duration.

Because stdout is a normal pipe, a Python consumer must keep reading it. If the
consumer stops, the bridge will eventually block on a full pipe; this transport
does not automatically discard old scans.

