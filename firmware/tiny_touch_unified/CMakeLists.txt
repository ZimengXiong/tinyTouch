cmake_minimum_required(VERSION 3.16)

# Local hardware recovery only. Release builds leave this OFF and therefore
# require a matching enrolled fingerprint for every protected operation.
option(TINYTOUCH_DEVELOPMENT_SKIP_FINGERPRINT_AUTH
       "Allow local development firmware to bypass fingerprint authorization"
       OFF)
option(TINYTOUCH_RECOVERY_BUILD
       "Build one-time firmware that clears the fingerprint sensor and device state"
       OFF)

set(TINYTOUCH_VERSION_FILE "${CMAKE_CURRENT_LIST_DIR}/../../VERSION")
set_property(DIRECTORY APPEND PROPERTY CMAKE_CONFIGURE_DEPENDS "${TINYTOUCH_VERSION_FILE}")
file(READ "${TINYTOUCH_VERSION_FILE}" TINYTOUCH_VERSION)
string(STRIP "${TINYTOUCH_VERSION}" TINYTOUCH_VERSION)
set(PROJECT_VER "${TINYTOUCH_VERSION}")

# A manually dispatched release runs from main but checks out the selected
# tag. GITHUB_SHA still names main, so prefer the code actually being built.
execute_process(
  COMMAND git rev-parse --verify HEAD
  WORKING_DIRECTORY "${CMAKE_CURRENT_LIST_DIR}/../.."
  OUTPUT_VARIABLE TINYTOUCH_SOURCE_COMMIT
  OUTPUT_STRIP_TRAILING_WHITESPACE
  ERROR_QUIET
)
# Containerized checkouts may not permit Git to read the worktree. The release
# workflow supplies the validated target separately from its dispatch SHA.
if(NOT TINYTOUCH_SOURCE_COMMIT AND DEFINED ENV{RELEASE_COMMIT})
  set(TINYTOUCH_SOURCE_COMMIT "$ENV{RELEASE_COMMIT}")
endif()
if(TINYTOUCH_SOURCE_COMMIT)
  string(SUBSTRING "${TINYTOUCH_SOURCE_COMMIT}" 0 12 TINYTOUCH_BUILD_ID)
elseif(DEFINED ENV{GITHUB_SHA} AND NOT "$ENV{GITHUB_SHA}" STREQUAL "")
  string(SUBSTRING "$ENV{GITHUB_SHA}" 0 12 TINYTOUCH_BUILD_ID)
endif()
if(NOT TINYTOUCH_BUILD_ID)
  set(TINYTOUCH_BUILD_ID "development")
endif()
message(STATUS "tinyTouch build ${TINYTOUCH_BUILD_ID}")

include($ENV{IDF_PATH}/tools/cmake/project.cmake)
if(NOT (IDF_VERSION_MAJOR EQUAL 5 AND IDF_VERSION_MINOR EQUAL 3))
  message(FATAL_ERROR
    "tinyTouch requires ESP-IDF 5.3.x; found ${IDF_VERSION}. "
    "Install or select ESP-IDF 5.3 before building.")
endif()
project(tiny_touch_unified)
