with open('C:/Users/gruto/OneDrive/Desktop/ATM/dashboard/index.html', encoding='utf-8') as f:
    lines = f.readlines()

issues = []
for i, line in enumerate(lines, 1):
    stripped = line.strip()
    backtick_count = stripped.count('`')
    if backtick_count >= 2 and '${' in stripped:
        issues.append((i, stripped[:120]))

if issues:
    print("Potential nested backtick issues:")
    for ln, txt in issues:
        print("  Line %d: %s" % (ln, txt))
else:
    print("No nested backtick issues found")

# Также проверим незакрытые template literals
depth = 0
in_template = False
for i, line in enumerate(lines, 1):
    for ch in line:
        if ch == '`':
            in_template = not in_template

print("Template literal open at end of file:", in_template)
