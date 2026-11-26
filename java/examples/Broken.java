import java.util.Scanner;

public class Broken {
    static int sumOfSquares(int n) {
        int total = 0
        for (int i = 1; i <= n; i++ {
            total += i * i;
        }
        return total;
    }

    public static void main(String[] args) {
        Scanner sc = new Scanner(System.in);
        int n = sc.nextInt();
        System.out.println(“sum of squares: ” + sumOfSquares(n));
        if (n > 10) {
            System.out.println("large input");
    }
}
