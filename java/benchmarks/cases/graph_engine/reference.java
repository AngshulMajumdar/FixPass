import java.util.ArrayDeque;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.Deque;
import java.util.List;
import java.util.PriorityQueue;
import java.util.Scanner;

public class Main {

    static int n;
    static List<int[]>[] adj;
    static boolean directed;

    @SuppressWarnings({"unchecked", "rawtypes"})
    static void init(int nodes, boolean dir) {
        n = nodes;
        directed = dir;
        adj = new List[n];
        for (int i = 0; i < n; i++) {
            adj[i] = new ArrayList<>();
        }
    }

    static void addEdge(int u, int v, int w) {
        adj[u].add(new int[] {v, w});
        if (!directed) {
            adj[v].add(new int[] {u, w});
        }
    }

    static int[] bfs(int src) {
        int[] dist = new int[n];
        Arrays.fill(dist, -1);
        Deque<Integer> q = new ArrayDeque<>();
        dist[src] = 0;
        q.add(src);
        while (!q.isEmpty()) {
            int u = q.poll();
            for (int[] e : adj[u]) {
                int v = e[0];
                if (dist[v] == -1) {
                    dist[v] = dist[u] + 1;
                    q.add(v);
                }
            }
        }
        return dist;
    }

    static long[] dijkstra(int src) {
        long[] dist = new long[n];
        Arrays.fill(dist, Long.MAX_VALUE);
        dist[src] = 0;
        PriorityQueue<long[]> pq = new PriorityQueue<>((a, b) -> Long.compare(a[0], b[0]));
        pq.add(new long[] {0, src});
        while (!pq.isEmpty()) {
            long[] top = pq.poll();
            int u = (int) top[1];
            if (top[0] > dist[u]) {
                continue;
            }
            for (int[] e : adj[u]) {
                int v = e[0];
                long nd = dist[u] + e[1];
                if (nd < dist[v]) {
                    dist[v] = nd;
                    pq.add(new long[] {nd, v});
                }
            }
        }
        return dist;
    }

    static int components() {
        boolean[] seen = new boolean[n];
        int count = 0;
        for (int s = 0; s < n; s++) {
            if (seen[s]) {
                continue;
            }
            count++;
            Deque<Integer> stack = new ArrayDeque<>();
            stack.push(s);
            seen[s] = true;
            while (!stack.isEmpty()) {
                int u = stack.pop();
                for (int[] e : adj[u]) {
                    if (!seen[e[0]]) {
                        seen[e[0]] = true;
                        stack.push(e[0]);
                    }
                }
            }
        }
        return count;
    }

    static List<Integer> topoSort() {
        int[] indeg = new int[n];
        for (int u = 0; u < n; u++) {
            for (int[] e : adj[u]) {
                indeg[e[0]]++;
            }
        }
        PriorityQueue<Integer> ready = new PriorityQueue<>();
        for (int u = 0; u < n; u++) {
            if (indeg[u] == 0) {
                ready.add(u);
            }
        }
        List<Integer> order = new ArrayList<>();
        while (!ready.isEmpty()) {
            int u = ready.poll();
            order.add(u);
            for (int[] e : adj[u]) {
                indeg[e[0]]--;
                if (indeg[e[0]] == 0) {
                    ready.add(e[0]);
                }
            }
        }
        if (order.size() < n) {
            return null;
        }
        return order;
    }

    static int maxDegree() {
        int best = 0;
        for (int u = 0; u < n; u++) {
            if (adj[u].size() > adj[best].size()) {
                best = u;
            }
        }
        return best;
    }

    static long totalWeight() {
        long sum = 0;
        for (int u = 0; u < n; u++) {
            for (int[] e : adj[u]) {
                sum += e[1];
            }
        }
        return directed ? sum : sum / 2;
    }

    static String joinInts(int[] values) {
        StringBuilder sb = new StringBuilder();
        for (int i = 0; i < values.length; i++) {
            if (i > 0) {
                sb.append(' ');
            }
            sb.append(values[i]);
        }
        return sb.toString();
    }

    public static void main(String[] args) {
        Scanner sc = new Scanner(System.in);
        int nodes = sc.nextInt();
        int m = sc.nextInt();
        boolean dir = sc.next().equals("D");
        init(nodes, dir);
        for (int i = 0; i < m; i++) {
            int u = sc.nextInt();
            int v = sc.nextInt();
            int w = sc.nextInt();
            if (u < 0 || u >= n || v < 0 || v >= n) {
                System.out.println("SKIP " + u + " " + v);
                continue;
            }
            addEdge(u, v, w);
        }
        while (sc.hasNext()) {
            String cmd = sc.next();
            if (cmd.equals("BFS")) {
                int s = sc.nextInt();
                System.out.println("BFS " + joinInts(bfs(s)));
            } else if (cmd.equals("DIJ")) {
                int s = sc.nextInt();
                long[] d = dijkstra(s);
                StringBuilder sb = new StringBuilder("DIJ");
                for (long x : d) {
                    sb.append(' ').append(x == Long.MAX_VALUE ? "INF" : String.valueOf(x));
                }
                System.out.println(sb);
            } else if (cmd.equals("COMP")) {
                System.out.println("COMP " + components());
            } else if (cmd.equals("TOPO")) {
                List<Integer> order = topoSort();
                if (order == null) {
                    System.out.println("TOPO CYCLE");
                } else {
                    StringBuilder sb = new StringBuilder("TOPO");
                    for (int x : order) {
                        sb.append(' ').append(x);
                    }
                    System.out.println(sb);
                }
            } else if (cmd.equals("MAXDEG")) {
                int u = maxDegree();
                System.out.println("MAXDEG " + u + " " + adj[u].size());
            } else if (cmd.equals("WEIGHT")) {
                System.out.println("WEIGHT " + totalWeight());
            } else {
                System.out.println("UNKNOWN " + cmd);
            }
        }
    }
}
