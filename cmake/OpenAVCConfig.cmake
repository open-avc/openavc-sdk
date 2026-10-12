include(CMakeFindDependencyMacro)
find_dependency(Threads)
find_dependency(OpenSSL 3)
include("${CMAKE_CURRENT_LIST_DIR}/OpenAVCTargets.cmake")
