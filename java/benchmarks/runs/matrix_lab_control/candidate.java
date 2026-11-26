import java.util.HashMap;
import java.util.Map;
import java.util.Scanner;

public class Main {

    static Map<String, long[][]> store = new HashMap<>();

    static long[][] read(Scanner sc) {
        int r = sc.nextInt();
        int c = sc.nextInt();
        long[][] m = new long[r][c];
        for (int i = 0; i < r; i++) {
            for (int j = 0; j < c; j++) {
                m[i][j] = sc.nextLong();
            }
        }
        return m;
    }

    static void print(String label, long[][] m) {
        System.out.println(label + " " + m.length + "x" + (m.length == 0 ? 0 : m[0].length));
        for (long[] row : m) {
            StringBuilder sb = new StringBuilder();
            for (int j = 0; j < row.length; j++) {
                if (j > 0) {
                    sb.append(' ');
                }
                sb.append(row[j]);
            }
            System.out.println(sb);
        }
    }

    static long[][] multiply(long[][] a, long[][] b) {
        int n = a.length;
        int k = b.length;
        int m = b[0].length;
        long[][] out = new long[n][m];
        for (int i = 0; i < n; i++) {
            for (int j = 0; j < m; j++) {
                long s = 0;
                for (int t = 0; t < k; t++) {
                    s += a[i][t] * b[t][j];
                }
                out[i][j] = s;
            }
        }
        return out;
    }

    static long[][] add(long[][] a, long[][] b, int sign) {
        long[][] out = new long[a.length][a[0].length];
        for (int i = 0; i < a.length; i++) {
            for (int j = 0; j < a[0].length; j++) {
                out[i][j] = a[i][j] + sign * b[i][j];
            }
        }
        return out;
    }

    static long[][] transpose(long[][] a) {
        int r = a.length;
        int c = a[0].length;
        long[][] out = new long[c][r];
        for (int i = 0; i < r; i++) {
            for (int j = 0; j < c; j++) {
                out[j][i] = a[i][j];
            }
        }
        return out;
    }

    static long trace(long[][] a) {
        long t = 0;
        for (int i = 0; i < Math.min(a.length, a[0].length); i++) {
            t += a[i][i];
        }
        return t;
    }

    static long[][] minor(long[][] a, int row, int col) {
        int n = a.length;
        long[][] out = new long[n - 1][n - 1];
        int r = 0;
        for (int i = 0; i < n; i++) {
            if (i == row) {
                continue;
            }
            int c = 0;
            for (int j = 0; j < n; j++) {
                if (j == col) {
                    continue;
                }
                out[r][c] = a[i][j];
                c++;
            }
            r++;
        }
        return out;
    }

    static long determinant(long[][] a) {
        int n = a.length;
        if (n == 1) {
            return a[0][0];
        }
        if (n == 2) {
            return a[0][0] * a[1][1] - a[0][1] * a[1][0];
        }
        long det = 0;
        for (int j = 0; j < n; j++) {
            long sign = (j % 2 == 0) ? 1 : -1;
            det += sign * a[0][j] * determinant(minor(a, 0, j));
        }
        return det;
    }

    static long[][] power(long[][] a, int e) {
        int n = a.length;
        long[][] result = new long[n][n];
        for (int i = 0; i < n; i++) {
            result[i][i] = 1;
        }
        long[][] base = a;
        while (e > 0) {
            if ((e & 1) == 1) {
                result = multiply(result, base);
            }
            base = multiply(base, base);
            e >>= 1;
        }
        return result;
    }

    static long[] rowSums(long[][] a) {
        long[] out = new long[a.length];
        for (int i = 0; i < a.length; i++) {
            for (int j = 0; j < a[i].length; j++) {
                out[i] += a[i][j];
            }
        }
        return out;
    }

    static boolean symmetric(long[][] a) {
        if (a.length != a[0].length) {
            return false;
        }
        for (int i = 0; i < a.length; i++) {
            for (int j = i + 1; j < a.length; j++) {
                if (a[i][j] != a[j][i]) {
                    return false;
                }
            }
        }
        return true;
    }

    static long[][] get(String name) {
        long[][] m = store.get(name);
        if (m == null) {
            System.out.println("ERROR unknown " + name);
        }
        return m;
    }

    public static void main(String[] args) {
        Scanner sc = new Scanner(System.in);
        while (sc.hasNext()) {
            String cmd = sc.next();
            if (cmd.equals("DEF")) {
                String name = sc.next();
                store.put(name, read(sc));
                System.out.println("DEF " + name);
            } else if (cmd.equals("MUL") || cmd.equals("ADD") || cmd.equals("SUB")) {
                String dst = sc.next();
                long[][] a = get(sc.next());
                long[][] b = get(sc.next());
                if (a == null || b == null) {
                    continue;
                }
                long[][] r;
                if (cmd.equals("MUL")) {
                    if (a[0].length != b.length) {
                        System.out.println("ERROR shape");
                        continue;
                    }
                    r = multiply(a, b);
                } else {
                    if (a.length != b.length || a[0].length != b[0].length) {
                        System.out.println("ERROR shape");
                        continue;
                    }
                    r = add(a, b, cmd.equals("ADD") ? 1 : -1);
                }
                store.put(dst, r);
                print(dst, r);
            } else if (cmd.equals("T")) {
                String dst = sc.next();
                long[][] a = get(sc.next());
                if (a == null) {
                    continue;
                }
                long[][] r = transpose(a);
                store.put(dst, r);
                print(dst, r);
            } else if (cmd.equals("POW")) {
                String dst = sc.next();
                long[][] a = get(sc.next());
                int e = sc.nextInt();
                if (a == null) {
                    continue;
                }
                if (a.length != a[0].length) {
                    System.out.println("ERROR shape");
                    continue;
                }
                long[][] r = power(a, e);
                store.put(dst, r);
                print(dst, r);
            } else if (cmd.equals("INFO")) {
                String name = sc.next();
                long[][] a = get(name);
                if (a == null) {
                    continue;
                }
                StringBuilder sb = new StringBuilder("INFO " + name + " trace=" + trace(a));
                if (a.length == a[0].length) {
                    sb.append(" det=").append(determinant(a));
                }
                sb.append(" sym=").append(symmetric(a) ? "yes" : "no");
                long[] rs = rowSums(a);
                sb.append(" rows=");
                for (int i = 0; i < rs.length; i++) {
                    sb.append(i == 0 ? "" : ",").append(rs[i]);
                }
                System.out.println(sb);
            } else {
                System.out.println("UNKNOWN " + cmd);
            }
        }
    }
}
