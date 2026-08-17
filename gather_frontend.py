import os

source_dir = "pwa-frontend"
output_file = "frontend_code.txt"

include_extensions = {".js", ".jsx", ".css", ".html", ".json"}
exclude_dirs = {"node_modules", "dist", "build", ".git", ".vite", ".vscode", "assets"}

with open(output_file, "w", encoding="utf-8") as outfile:
    for root, dirs, files in os.walk(source_dir):
        dirs[:] = [d for d in dirs if d not in exclude_dirs]
        for file in files:
            if file.endswith(tuple(include_extensions)):
                if "package-lock" in file:
                    continue
                file_path = os.path.join(root, file)
                outfile.write("\n\n" + "="*80 + "\n")
                outfile.write(f"FILE: {file_path}\n")
                outfile.write("="*80 + "\n\n")
                try:
                    with open(file_path, "r", encoding="utf-8") as infile:
                        outfile.write(infile.read())
                except Exception as e:
                    outfile.write(f"Error reading file: {e}\n")
print("Done")
