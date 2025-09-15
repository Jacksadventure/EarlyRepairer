import subprocess
import os

# Paths (adjust as needed)
repairer = "./earleyrepairer"
parser = "project/erepair-subjects/ini/ini"
input_file = "test.ini"
output_file = "test_repair_output.ini"

# Remove output file if it exists
if os.path.exists(output_file):
    os.remove(output_file)

cmd = f"{repairer} {parser} {input_file} {output_file}"
print("Running:", cmd)

result = subprocess.run(cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

print("Return code:", result.returncode)
print("STDOUT:")
print(result.stdout)
print("STDERR:")
print(result.stderr)

if os.path.exists(output_file):
    print(f"\nOutput file '{output_file}' created. Contents:")
    with open(output_file, "r", encoding="utf-8") as f:
        print(f.read())
else:
    print(f"\nOutput file '{output_file}' was NOT created.")
