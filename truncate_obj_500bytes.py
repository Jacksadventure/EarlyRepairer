import os

obj_dir = "original_files/obj_data"
max_bytes = 50

for filename in os.listdir(obj_dir):
    if filename.endswith(".obj"):
        path = os.path.join(obj_dir, filename)
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
