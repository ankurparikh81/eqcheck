// Reference implementation of vector math
typedef int my_int_t;

my_int_t compute_dot(my_int_t x1, my_int_t y1, my_int_t x2, my_int_t y2) {
    /* Calculate products */
    my_int_t p1 = x1 * x2;
    my_int_t p2 = y1 * y2;
    // Return dot product sum
    return p1 + p2;
}
