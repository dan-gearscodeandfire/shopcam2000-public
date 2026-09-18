# ".." puts firmware/common on the include path so every project can pick up
# the gitignored twab_secrets.h from one place instead of keeping three copies
# of the same key in sync.
idf_component_register(
    SRCS "twab_link.cpp"
    INCLUDE_DIRS "include" ".."
    REQUIRES mbedtls
)
