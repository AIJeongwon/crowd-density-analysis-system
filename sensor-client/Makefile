CXX ?= g++
CXXFLAGS ?= -O2
CXXFLAGS += -std=c++17 -Wall -Wextra -Wpedantic

BUILD_DIR ?= build
RPLIDAR_SDK_DIR ?= ../rplidar_sdk
TARGET := $(BUILD_DIR)/rplidar_c1_bridge
SOURCE := rplidar_c1_bridge.cpp

.PHONY: all clean check-sdk

all: check-sdk $(TARGET)

check-sdk:
	@test -n "$(RPLIDAR_SDK_DIR)" || { \
		echo "Set RPLIDAR_SDK_DIR to the official rplidar_sdk checkout." >&2; \
		exit 2; \
	}
	@test -f "$(RPLIDAR_SDK_DIR)/sdk/include/sl_lidar.h" || { \
		echo "Invalid RPLIDAR_SDK_DIR: sdk/include/sl_lidar.h was not found." >&2; \
		exit 2; \
	}

$(RPLIDAR_SDK_DIR)/output/Linux/Release/libsl_lidar_sdk.a:
	$(MAKE) -C "$(RPLIDAR_SDK_DIR)"

$(TARGET): $(SOURCE) $(RPLIDAR_SDK_DIR)/output/Linux/Release/libsl_lidar_sdk.a
	mkdir -p "$(BUILD_DIR)"
	$(CXX) $(CXXFLAGS) \
		-I"$(RPLIDAR_SDK_DIR)/sdk/include" \
		-I"$(RPLIDAR_SDK_DIR)/sdk/src" \
		$(SOURCE) \
		"$(RPLIDAR_SDK_DIR)/output/Linux/Release/libsl_lidar_sdk.a" \
		-pthread -lrt \
		-o "$(TARGET)"

clean:
	$(RM) -r "$(BUILD_DIR)"
