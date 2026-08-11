#include <sl_lidar.h>
#include <sl_lidar_driver.h>

#if SL_LIDAR_SDK_VERSION_MAJOR < 2 || \
    (SL_LIDAR_SDK_VERSION_MAJOR == 2 && SL_LIDAR_SDK_VERSION_MINOR < 1)
#error "RPLIDAR C1 requires SLAMTEC SDK 2.1.0 or newer"
#endif

#include <algorithm>
#include <charconv>
#include <chrono>
#include <cmath>
#include <csignal>
#include <cstdint>
#include <iomanip>
#include <iostream>
#include <locale>
#include <memory>
#include <sstream>
#include <string>
#include <system_error>
#include <vector>

namespace {

constexpr int kExitUsage = 2;
constexpr int kExitDevice = 3;
constexpr int kExitStream = 4;
constexpr std::size_t kNodeCapacity = 8192;

volatile std::sig_atomic_t g_stop_requested = 0;

struct Options {
    std::string port;
    int baudrate = 460800;
    std::string scan_mode = "Standard";
    int timeout_ms = 2000;
    int max_consecutive_timeouts = 3;
};

enum class ParseResult {
    Run,
    ExitSuccess,
    ExitFailure,
};

void requestStop(int) {
    g_stop_requested = 1;
}

void printUsage(const char* executable, std::ostream& output) {
    output
        << "Usage: " << executable << " --port DEVICE [options]\n"
        << "       " << executable << " DEVICE [options]\n\n"
        << "SLAMTEC RPLIDAR C1 bridge. One JSON object is written to stdout per line.\n\n"
        << "Options:\n"
        << "  --port DEVICE          Serial device, for example /dev/ttyUSB0\n"
        << "  --baud RATE            UART baud rate (default: 460800)\n"
        << "  --scan-mode NAME       SDK scan mode name (default: Standard)\n"
        << "  --timeout-ms MS        SDK operation timeout (default: 2000)\n"
        << "  --max-timeouts COUNT   Stop after this many consecutive scan timeouts\n"
        << "                         (default: 3)\n"
        << "  -h, --help             Show this help without opening the device\n\n"
        << "Streaming stdout contains a ready record followed by scan records.\n"
        << "All diagnostics are written to stderr. SIGINT and SIGTERM stop the scan.\n";
}

bool parseBoundedInt(
    const std::string& text,
    int minimum,
    int maximum,
    int& value) {
    long long parsed = 0;
    const char* begin = text.data();
    const char* end = begin + text.size();
    const auto result = std::from_chars(begin, end, parsed);

    if (result.ec != std::errc() || result.ptr != end ||
        parsed < minimum || parsed > maximum) {
        return false;
    }

    value = static_cast<int>(parsed);
    return true;
}

ParseResult parseArguments(int argc, char** argv, Options& options) {
    for (int index = 1; index < argc; ++index) {
        const std::string argument = argv[index];

        if (argument == "-h" || argument == "--help") {
            printUsage(argv[0], std::cout);
            return ParseResult::ExitSuccess;
        }

        auto requireValue = [&](const char* option_name) -> const char* {
            if (index + 1 >= argc) {
                std::cerr << "Missing value for " << option_name << ".\n";
                return nullptr;
            }
            return argv[++index];
        };

        if (argument == "--port") {
            const char* value = requireValue("--port");
            if (value == nullptr) {
                return ParseResult::ExitFailure;
            }
            options.port = value;
        } else if (argument == "--baud") {
            const char* value = requireValue("--baud");
            if (value == nullptr ||
                !parseBoundedInt(value, 1200, 4000000, options.baudrate)) {
                std::cerr << "--baud must be an integer from 1200 to 4000000.\n";
                return ParseResult::ExitFailure;
            }
        } else if (argument == "--scan-mode") {
            const char* value = requireValue("--scan-mode");
            if (value == nullptr) {
                return ParseResult::ExitFailure;
            }
            options.scan_mode = value;
            if (options.scan_mode.empty() || options.scan_mode.size() > 63) {
                std::cerr << "--scan-mode must contain 1 to 63 bytes.\n";
                return ParseResult::ExitFailure;
            }
        } else if (argument == "--timeout-ms") {
            const char* value = requireValue("--timeout-ms");
            if (value == nullptr ||
                !parseBoundedInt(value, 100, 60000, options.timeout_ms)) {
                std::cerr << "--timeout-ms must be an integer from 100 to 60000.\n";
                return ParseResult::ExitFailure;
            }
        } else if (argument == "--max-timeouts") {
            const char* value = requireValue("--max-timeouts");
            if (value == nullptr ||
                !parseBoundedInt(
                    value,
                    1,
                    1000,
                    options.max_consecutive_timeouts)) {
                std::cerr << "--max-timeouts must be an integer from 1 to 1000.\n";
                return ParseResult::ExitFailure;
            }
        } else if (!argument.empty() && argument.front() == '-') {
            std::cerr << "Unknown option: " << argument << "\n";
            return ParseResult::ExitFailure;
        } else if (options.port.empty()) {
            options.port = argument;
        } else {
            std::cerr << "Unexpected positional argument: " << argument << "\n";
            return ParseResult::ExitFailure;
        }
    }

    if (options.port.empty()) {
        std::cerr << "A serial device is required. Use --port DEVICE.\n\n";
        printUsage(argv[0], std::cerr);
        return ParseResult::ExitFailure;
    }

    return ParseResult::Run;
}

std::string resultCode(sl_result result) {
    std::ostringstream output;
    output << "0x" << std::hex << std::uppercase
           << static_cast<std::uint32_t>(result);
    return output.str();
}

std::string jsonString(const std::string& value) {
    std::ostringstream output;
    output.imbue(std::locale::classic());
    output << '"';

    for (const unsigned char character : value) {
        switch (character) {
            case '"':
                output << "\\\"";
                break;
            case '\\':
                output << "\\\\";
                break;
            case '\b':
                output << "\\b";
                break;
            case '\f':
                output << "\\f";
                break;
            case '\n':
                output << "\\n";
                break;
            case '\r':
                output << "\\r";
                break;
            case '\t':
                output << "\\t";
                break;
            default:
                if (character < 0x20) {
                    output << "\\u00" << std::hex << std::setw(2)
                           << std::setfill('0') << static_cast<unsigned>(character)
                           << std::dec << std::setfill(' ');
                } else {
                    output << static_cast<char>(character);
                }
        }
    }

    output << '"';
    return output.str();
}

template <std::size_t Size>
std::string boundedString(const char (&value)[Size]) {
    const char* end = std::find(value, value + Size, '\0');
    return std::string(value, end);
}

std::string serialNumber(const sl_lidar_response_device_info_t& info) {
    std::ostringstream output;
    output << std::hex << std::setfill('0');
    for (const sl_u8 byte : info.serialnum) {
        output << std::setw(2) << static_cast<unsigned>(byte);
    }
    return output.str();
}

std::string firmwareVersion(const sl_lidar_response_device_info_t& info) {
    std::ostringstream output;
    output << static_cast<unsigned>(info.firmware_version >> 8) << '.'
           << static_cast<unsigned>(info.firmware_version & 0xFF);
    return output.str();
}

std::int64_t unixTimeMilliseconds() {
    return std::chrono::duration_cast<std::chrono::milliseconds>(
               std::chrono::system_clock::now().time_since_epoch())
        .count();
}

std::int64_t monotonicMicroseconds() {
    return std::chrono::duration_cast<std::chrono::microseconds>(
               std::chrono::steady_clock::now().time_since_epoch())
        .count();
}

class DriverSession {
public:
    DriverSession(sl::ILidarDriver& driver, sl_u32 timeout_ms)
        : driver_(driver), timeout_ms_(timeout_ms) {}

    DriverSession(const DriverSession&) = delete;
    DriverSession& operator=(const DriverSession&) = delete;

    ~DriverSession() {
        if (scan_command_issued_) {
            const sl_result result = driver_.stop(timeout_ms_);
            if (SL_IS_FAIL(result)) {
                std::cerr << "Warning: SDK stop failed during shutdown ("
                          << resultCode(result) << ").\n";
            }
            const sl_result motor_result = driver_.setMotorSpeed(0);
            if (SL_IS_FAIL(motor_result)) {
                std::cerr << "Warning: motor stop command failed during shutdown ("
                          << resultCode(motor_result) << ").\n";
            }
        }
        if (connected_) {
            driver_.disconnect();
        }
    }

    void markConnected() noexcept {
        connected_ = true;
    }

    void markScanCommandIssued() noexcept {
        scan_command_issued_ = true;
    }

private:
    sl::ILidarDriver& driver_;
    sl_u32 timeout_ms_;
    bool connected_ = false;
    bool scan_command_issued_ = false;
};

bool writeReadyRecord(
    const sl_lidar_response_device_info_t& info,
    const sl::LidarScanMode& mode,
    const Options& options) {
    std::ostringstream record;
    record.imbue(std::locale::classic());
    record << std::setprecision(9)
           << "{\"type\":\"ready\""
           << ",\"timestamp_unix_ms\":" << unixTimeMilliseconds()
           << ",\"port\":" << jsonString(options.port)
           << ",\"baudrate\":" << options.baudrate
           << ",\"device\":{\"model\":" << static_cast<unsigned>(info.model)
           << ",\"firmware\":" << jsonString(firmwareVersion(info))
           << ",\"hardware\":" << static_cast<unsigned>(info.hardware_version)
           << ",\"serial\":" << jsonString(serialNumber(info)) << '}'
           << ",\"sdk_version\":"
           << jsonString(
                  std::to_string(SL_LIDAR_SDK_VERSION_MAJOR) + "." +
                  std::to_string(SL_LIDAR_SDK_VERSION_MINOR) + "." +
                  std::to_string(SL_LIDAR_SDK_VERSION_PATCH))
           << ",\"scan_mode\":{\"id\":" << mode.id
           << ",\"name\":" << jsonString(boundedString(mode.scan_mode))
           << ",\"us_per_sample\":" << mode.us_per_sample
           << ",\"max_distance_m\":" << mode.max_distance
           << ",\"answer_type\":" << static_cast<unsigned>(mode.ans_type)
           << "}}";

    std::cout << record.str() << '\n' << std::flush;
    return std::cout.good();
}

bool writeScanRecord(
    std::uint64_t sequence,
    const sl_lidar_response_measurement_node_hq_t* nodes,
    std::size_t count,
    const sl::LidarScanMode& mode) {
    double scan_hz = 0.0;
    bool scan_hz_is_valid = false;
    if (count > 0 && std::isfinite(mode.us_per_sample) &&
        mode.us_per_sample > 0.0F) {
        scan_hz = 1000000.0 /
                  (static_cast<double>(count) *
                   static_cast<double>(mode.us_per_sample));
        scan_hz_is_valid = std::isfinite(scan_hz) && scan_hz > 0.0;
    }

    std::size_t valid_point_count = 0;
    for (std::size_t index = 0; index < count; ++index) {
        if (nodes[index].dist_mm_q2 != 0) {
            ++valid_point_count;
        }
    }

    std::ostringstream record;
    record.imbue(std::locale::classic());
    record << std::setprecision(9)
           << "{\"type\":\"scan\""
           << ",\"sequence\":" << sequence
           << ",\"timestamp_unix_ms\":" << unixTimeMilliseconds()
           << ",\"monotonic_us\":" << monotonicMicroseconds()
           << ",\"scan_mode\":" << jsonString(boundedString(mode.scan_mode))
           << ",\"scan_hz\":";

    if (scan_hz_is_valid) {
        record << scan_hz;
    } else {
        record << "null";
    }

    record << ",\"raw_point_count\":" << count
           << ",\"point_count\":" << valid_point_count
           << ",\"points\":[";

    bool first_point = true;
    for (std::size_t index = 0; index < count; ++index) {
        const auto& node = nodes[index];
        if (node.dist_mm_q2 == 0) {
            continue;
        }

        const double angle_deg =
            static_cast<double>(node.angle_z_q14) * 90.0 / 16384.0;
        const double distance_mm =
            static_cast<double>(node.dist_mm_q2) / 4.0;

        if (!std::isfinite(angle_deg) || !std::isfinite(distance_mm)) {
            continue;
        }

        if (!first_point) {
            record << ',';
        }
        first_point = false;
        record << '[' << angle_deg << ',' << distance_mm << ','
               << static_cast<unsigned>(node.quality) << ']';
    }

    record << "]}";
    std::cout << record.str() << '\n' << std::flush;
    return std::cout.good();
}

int runBridge(const Options& options) {
    auto channel_result =
        sl::createSerialPortChannel(options.port, options.baudrate);
    if (!channel_result || *channel_result == nullptr) {
        std::cerr << "Could not create serial channel for " << options.port
                  << " (" << resultCode(channel_result.err) << ").\n";
        return kExitDevice;
    }

    // The channel must outlive the driver, so it is declared first.
    std::unique_ptr<sl::IChannel> channel(*channel_result);

    auto driver_result = sl::createLidarDriver();
    if (!driver_result || *driver_result == nullptr) {
        std::cerr << "Could not create SLAMTEC LiDAR driver ("
                  << resultCode(driver_result.err) << ").\n";
        return kExitDevice;
    }
    std::unique_ptr<sl::ILidarDriver> driver(*driver_result);

    const auto timeout_ms = static_cast<sl_u32>(options.timeout_ms);
    DriverSession session(*driver, timeout_ms);

    sl_result result = driver->connect(channel.get());
    if (SL_IS_FAIL(result)) {
        std::cerr << "Could not connect to " << options.port << " at "
                  << options.baudrate << " baud (" << resultCode(result)
                  << ").\n";
        return kExitDevice;
    }
    session.markConnected();

    sl_lidar_response_device_info_t device_info{};
    result = driver->getDeviceInfo(device_info, timeout_ms);
    if (SL_IS_FAIL(result)) {
        std::cerr << "GET_INFO failed (" << resultCode(result) << ").\n";
        return kExitDevice;
    }

    sl_lidar_response_device_health_t health{};
    result = driver->getHealth(health, timeout_ms);
    if (SL_IS_FAIL(result)) {
        std::cerr << "GET_HEALTH failed (" << resultCode(result) << ").\n";
        return kExitDevice;
    }
    if (health.status == SL_LIDAR_STATUS_ERROR) {
        std::cerr << "LiDAR reported protection/error state; error code "
                  << health.error_code << ". Reset or inspect the device before "
                  << "scanning.\n";
        return kExitDevice;
    }
    if (health.status == SL_LIDAR_STATUS_WARNING) {
        std::cerr << "Warning: LiDAR health status is WARNING; error code "
                  << health.error_code << ".\n";
    }

    std::vector<sl::LidarScanMode> modes;
    result = driver->getAllSupportedScanModes(modes, timeout_ms);
    if (SL_IS_FAIL(result)) {
        std::cerr << "Could not enumerate scan modes (" << resultCode(result)
                  << ").\n";
        return kExitDevice;
    }

    const auto selected = std::find_if(
        modes.begin(),
        modes.end(),
        [&](const sl::LidarScanMode& mode) {
            return boundedString(mode.scan_mode) == options.scan_mode;
        });

    if (selected == modes.end()) {
        std::cerr << "Scan mode " << options.scan_mode
                  << " is not supported. Available modes:";
        for (const auto& mode : modes) {
            std::cerr << ' ' << boundedString(mode.scan_mode);
        }
        std::cerr << "\n";
        return kExitDevice;
    }

    sl::LidarScanMode active_mode{};
    session.markScanCommandIssued();
    result = driver->startScanExpress(
        false,
        selected->id,
        0,
        &active_mode,
        timeout_ms);
    if (SL_IS_FAIL(result)) {
        std::cerr << "Could not start scan mode " << options.scan_mode << " ("
                  << resultCode(result) << ").\n";
        return kExitDevice;
    }
    if (!std::isfinite(active_mode.us_per_sample) ||
        active_mode.us_per_sample <= 0.0F ||
        !std::isfinite(active_mode.max_distance) ||
        active_mode.max_distance <= 0.0F) {
        std::cerr << "SDK returned invalid numeric metadata for scan mode "
                  << options.scan_mode << ".\n";
        return kExitDevice;
    }

    if (!writeReadyRecord(device_info, active_mode, options)) {
        std::cerr << "stdout closed while writing the ready record.\n";
        return kExitStream;
    }

    std::vector<sl_lidar_response_measurement_node_hq_t> nodes(kNodeCapacity);
    std::uint64_t sequence = 0;
    int consecutive_timeouts = 0;

    while (!g_stop_requested) {
        std::size_t count = nodes.size();
        result = driver->grabScanDataHq(nodes.data(), count, timeout_ms);

        if (g_stop_requested) {
            break;
        }

        if (result == SL_RESULT_OPERATION_TIMEOUT) {
            ++consecutive_timeouts;
            std::cerr << "Scan timeout " << consecutive_timeouts << '/'
                      << options.max_consecutive_timeouts << ".\n";
            if (consecutive_timeouts >= options.max_consecutive_timeouts) {
                std::cerr << "Too many consecutive scan timeouts; stopping.\n";
                return kExitStream;
            }
            continue;
        }
        if (SL_IS_FAIL(result)) {
            std::cerr << "grabScanDataHq failed (" << resultCode(result)
                      << ").\n";
            return kExitStream;
        }
        consecutive_timeouts = 0;

        result = driver->ascendScanData(nodes.data(), count);
        if (SL_IS_FAIL(result)) {
            std::cerr << "ascendScanData failed (" << resultCode(result)
                      << ").\n";
            return kExitStream;
        }

        if (!writeScanRecord(sequence++, nodes.data(), count, active_mode)) {
            std::cerr << "stdout closed while writing scan data.\n";
            return kExitStream;
        }
    }

    std::cerr << "Stop requested; shutting down LiDAR bridge.\n";
    return 0;
}

}  // namespace

int main(int argc, char** argv) {
    std::cout.imbue(std::locale::classic());
    std::cerr.imbue(std::locale::classic());

    Options options;
    const ParseResult parse_result = parseArguments(argc, argv, options);
    if (parse_result == ParseResult::ExitSuccess) {
        return 0;
    }
    if (parse_result == ParseResult::ExitFailure) {
        return kExitUsage;
    }

    std::signal(SIGINT, requestStop);
    std::signal(SIGTERM, requestStop);
#ifdef SIGPIPE
    std::signal(SIGPIPE, SIG_IGN);
#endif

    try {
        return runBridge(options);
    } catch (const std::exception& error) {
        std::cerr << "Fatal bridge error: " << error.what() << "\n";
        return kExitStream;
    } catch (...) {
        std::cerr << "Fatal bridge error: unknown exception.\n";
        return kExitStream;
    }
}
