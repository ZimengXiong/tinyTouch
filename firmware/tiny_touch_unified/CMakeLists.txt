cmake_minimum_required(VERSION 3.16)

option(TINYTOUCH_RECOVERY_BUILD "Build one-time destructive recovery firmware" OFF)

file(READ "${CMAKE_CURRENT_LIST_DIR}/../../VERSION" TINYTOUCH_VERSION)
string(STRIP "${TINYTOUCH_VERSION}" TINYTOUCH_VERSION)
set(PROJECT_VER "${TINYTOUCH_VERSION}")

execute_process(
  COMMAND git rev-parse --short=12 HEAD
  WORKING_DIRECTORY "${CMAKE_CURRENT_LIST_DIR}/../.."
  OUTPUT_VARIABLE TINYTOUCH_BUILD_ID
  OUTPUT_STRIP_TRAILING_WHITESPACE
)
if(NOT TINYTOUCH_BUILD_ID)
  set(TINYTOUCH_BUILD_ID "development")
endif()

include($ENV{IDF_PATH}/tools/cmake/project.cmake)
project(tiny_touch_unified)
