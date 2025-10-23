import sqlite3
from collections import defaultdict
import math

def main():
    db_path = "single.db"
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute("SELECT format, original_text FROM results")
    data = cursor.fetchall()
    conn.close()

    stats = defaultdict(list)
    for fmt, text in data:
        if text is not None:
            stats[fmt].append(len(text))

    print(f"{'Format':<12} {'AvgLength':>10} {'Variance':>10} {'StdDev':>10}")
    print("-" * 48)
    for fmt, lengths in stats.items():
        n = len(lengths)
        if n == 0:
            avg = var = stddev = 0
        else:
            avg = sum(lengths) / n
            var = sum((l - avg) ** 2 for l in lengths) / n
            stddev = math.sqrt(var)
        print(f"{fmt:<12} {avg:10.3f} {var:10.3f} {stddev:10.3f}")

if __name__ == "__main__":
    main()
