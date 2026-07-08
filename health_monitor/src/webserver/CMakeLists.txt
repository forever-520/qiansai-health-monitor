# health_monitor Web 后端桥接模块
#
# 依赖：CivetWeb (third_party/civetweb/civetweb.c + civetweb.h)
# 编译宏：NO_CGI NO_SSL USE_WEBSOCKET

add_library(webserver STATIC
    webserver.c
    vital_signs_state.c
)

target_include_directories(webserver
    PUBLIC
        $<BUILD_INTERFACE:${CMAKE_CURRENT_SOURCE_DIR}>
    PRIVATE
        ${CMAKE_SOURCE_DIR}/third_party/civetweb/include
)

# 嵌入 CivetWeb 源文件
target_sources(webserver PRIVATE
    ${CMAKE_SOURCE_DIR}/third_party/civetweb/civetweb.c
)

target_compile_definitions(webserver PRIVATE
    NO_CGI
    NO_SSL
    USE_WEBSOCKET
)

target_link_libraries(webserver PUBLIC
    pthread
    m
)

# RK3588 交叉编译额外配置
if(RK3588_CROSSCOMPILE)
    target_compile_definitions(webserver PRIVATE
        USE_STACK_SIZE=32768
    )
endif()
