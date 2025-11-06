cmake_minimum_required(VERSION 3.16)
project(lstar_cpp LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

# Options
option(LSTAR_BUILD_CLI "Build lstar CLI tool" ON)

# Include directories
include_directories(${CMAKE_CURRENT_SOURCE_DIR}/include)

# Library sources
set(LSTAR_SOURCES
  src/DFA.cpp
  src/ObservationTable.cpp
  src/LStar.cpp
)

add_library(lstar STATIC ${LSTAR_SOURCES})
target_include_directories(lstar PUBLIC ${CMAKE_CURRENT_SOURCE_DIR}/include)

if(LSTAR_BUILD_CLI)
  add_executable(lstar_cli apps/lstar_cli.cpp)
  target_link_libraries(lstar_cli PRIVATE lstar)
endif()

# Install (optional)
install(TARGETS lstar
        ARCHIVE DESTINATION lib
        LIBRARY DESTINATION lib
        RUNTIME DESTINATION bin)
install(DIRECTORY include/ DESTINATION include)
