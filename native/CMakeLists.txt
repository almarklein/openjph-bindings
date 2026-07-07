cmake_minimum_required(VERSION 3.24)
project(openjph-native LANGUAGES C CXX)

include(FetchContent)

set(BUILD_SHARED_LIBS         OFF CACHE BOOL "" FORCE)
set(OJPH_ENABLE_TIFF_SUPPORT  OFF CACHE BOOL "" FORCE)
set(OJPH_BUILD_TESTS          OFF CACHE BOOL "" FORCE)
set(OJPH_BUILD_EXECUTABLES    OFF CACHE BOOL "" FORCE)

FetchContent_Declare(openjph
  GIT_REPOSITORY https://github.com/aous72/OpenJPH.git
  GIT_TAG        0.29.0
  GIT_SHALLOW    TRUE)
FetchContent_MakeAvailable(openjph)

add_library(openjph_c SHARED src/openjph_c.cpp)

target_include_directories(openjph_c PRIVATE
  include
  $<TARGET_PROPERTY:openjph,INTERFACE_INCLUDE_DIRECTORIES>)

target_compile_features(openjph_c PRIVATE cxx_std_17)

# -fvisibility=hidden is GCC/Clang only; on MSVC symbol export is via __declspec.
target_compile_options(openjph_c PRIVATE
  $<$<NOT:$<CXX_COMPILER_ID:MSVC>>:-fvisibility=hidden>)

# Export symbols when building the DLL on Windows.
target_compile_definitions(openjph_c PRIVATE OPENJPH_C_EXPORTS)

# Embed the entire static OpenJPH archive so the shared library has no runtime
# dependency on a separate libopenjph. LINK_LIBRARY:WHOLE_ARCHIVE (CMake 3.24+)
# generates the correct flag for each platform (--whole-archive / -force_load /
# /WHOLEARCHIVE).
target_link_libraries(openjph_c PRIVATE "$<LINK_LIBRARY:WHOLE_ARCHIVE,openjph>")

install(TARGETS openjph_c LIBRARY DESTINATION lib)
install(FILES include/openjph_c.h DESTINATION include)

# Test drivers are opt-in; CI builds them with -fsanitize=address passed via
# CMAKE_<LANG>_FLAGS at configure time so the FetchContent'd OpenJPH objects
# inside openjph_c are instrumented too (see the native-sanitizer job).
option(OPENJPH_C_BUILD_TESTS "Build C API test drivers" OFF)
if(OPENJPH_C_BUILD_TESTS)
  enable_testing()
  add_executable(leak_check tests/leak_check.c)
  target_include_directories(leak_check PRIVATE include)
  target_link_libraries(leak_check PRIVATE openjph_c)
  add_test(NAME leak_check COMMAND leak_check)
endif()
