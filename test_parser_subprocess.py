import subprocess

parser = "project/erepair-subjects/ini/ini"
input_file = "tes.ini"

cmd = [parser, input_file]
print("Running:", " ".join(cmd))

proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
stdout, stderr = proc.communicate()

print("Return code:", proc.returncode)
print("STDOUT:")
print(stdout)
print("STDERR:")
print(stderr)
