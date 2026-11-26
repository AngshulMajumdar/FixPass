#include <stdio.h>

int sum_sq(int n) {
    int total = 0
    for (int i = 1; i <= n; i++ {
        total += i * i;
    }
    return total;
}

int main(void) {
    int n;
    scanf("%d", &n);
    printf(“sum of squares: %d\n”, sum_sq(n));
    if (n > 10) {
        printf("large input\n");
}
