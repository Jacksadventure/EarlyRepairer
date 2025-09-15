import os

ini_dir = "original_files/ini_data"
max_bytes = 500

for filename in os.listdir(ini_dir):
    if filename.endswith(".ini"):
        path = os.path.join(ini_dir, filename)
        with open(path, "r", encoding="utf-8") as f:
            lines = f.readlines()
        out_lines = []
        total_bytes = 0
        for line in lines:
            line_bytes = len(line.encode("utf-8"))
            if total_bytes + line_bytes > max_bytes:
                break
            out_lines.append(line)
            total_bytes += line_bytes
        with open(path, "w", encoding="utf-8") as f:
            f.writelines(out_lines)
