cmake_minimum_required(VERSION 3.24)

project(pyopenjph LANGUAGES C CXX)

# In monorepo builds the top-level CMakeLists.txt adds native/ before python/,
# so openjph_c already exists as a target. Skip discovery entirely in that case.
if(NOT TARGET openjph_c)

  # P1: read NATIVE_PATH from environment variable or python/.env
  if(DEFINED ENV{NATIVE_PATH})
    set(NATIVE_PATH "$ENV{NATIVE_PATH}")
  elseif(EXISTS "${CMAKE_CURRENT_SOURCE_DIR}/.env")
    file(READ "${CMAKE_CURRENT_SOURCE_DIR}/.env" _env_content)
    string(REGEX MATCH "NATIVE_PATH=([^\n\r]*)" _m "${_env_content}")
    if(CMAKE_MATCH_1)
      string(STRIP "${CMAKE_MATCH_1}" NATIVE_PATH)
    endif()
  endif()

  # Sibling detection: in monorepo and cibuildwheel wheel builds native/ sits
  # next to python/, so use it directly without requiring an env var.
  if(NOT (DEFINED NATIVE_PATH AND IS_DIRECTORY "${NATIVE_PATH}"))
    get_filename_component(_sibling_native
      "${CMAKE_CURRENT_SOURCE_DIR}/../native" ABSOLUTE)
    if(IS_DIRECTORY "${_sibling_native}")
      set(NATIVE_PATH "${_sibling_native}")
    endif()
  endif()

  if(DEFINED NATIVE_PATH AND IS_DIRECTORY "${NATIVE_PATH}")
    message(STATUS "pyopenjph: using NATIVE_PATH=${NATIVE_PATH}")
    add_subdirectory("${NATIVE_PATH}" native_build)
  else()
    # P2: download openjph-bindings from GitHub and use its native/ subdirectory.
    include(FetchContent)
    set(_BINDINGS_TAG "v0.1.0")
    message(STATUS "pyopenjph: NATIVE_PATH not set — downloading openjph-bindings ${_BINDINGS_TAG}")
    FetchContent_Declare(openjph_bindings
      GIT_REPOSITORY https://github.com/Clepio-Biotech/openjph-bindings
      GIT_TAG        ${_BINDINGS_TAG}
      GIT_SHALLOW    TRUE
      SOURCE_SUBDIR  native)
    FetchContent_MakeAvailable(openjph_bindings)
  endif()

endif()

install(TARGETS openjph_c
  LIBRARY DESTINATION openjph   # .so (Linux) and .dylib (macOS)
  RUNTIME DESTINATION openjph   # .dll (Windows)
)
