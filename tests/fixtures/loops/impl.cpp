int sum_range(int limit) {
    int total = 0;
    int idx = 0;
    while (idx < limit) {
        total = total + idx;
        idx++;
    }
    return total;
}
