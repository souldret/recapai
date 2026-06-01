"""
Fix: Remove ctx parameter from sub-widget classes that don't actually use it.
The refactoring script incorrectly added ctx to ALL classes with __init__(self, parent=None).
"""
import re

# Map of (file, class_name) pairs that should NOT have ctx
fixes = [
    # images_page.py
    ("ui/pages/images_page.py", "ImagePreviewPanel"),
    ("ui/pages/images_page.py", "ThumbnailWorker"),
    # analysis_page.py
    ("ui/pages/analysis_page.py", "ImageStatusItem"),
    # home_page.py
    ("ui/pages/home_page.py", "RecentProjectItem"),
    # project_page.py
    ("ui/pages/project_page.py", "NewProjectDialog"),
    # render_page.py
    ("ui/pages/render_page.py", "AccordionSection"),
    # script_page.py
    ("ui/pages/script_page.py", "EditSegmentCommand"),
    # tts_page.py
    ("ui/pages/tts_page.py", "FilteredVoiceCombo"),
    ("ui/pages/tts_page.py", "SegmentListItem"),
]

for filepath, cls_name in fixes:
    with open(filepath, "r", encoding="utf-8") as f:
        content = f.read()
    
    original = content
    
    # Pattern 1: def __init__(self, ctx: AppContext, ...) -> None:
    #         super().__init__(...)
    #         self.ctx = ctx
    # Replace with: def __init__(self, ...) -> None:
    #         super().__init__(...)
    
    # Remove "ctx: AppContext, " from __init__ signature (only within the class)
    # We need to find the class and its __init__
    
    # Find the class definition
    class_pattern = rf'(class {cls_name}\(.*?\):.*?)(def __init__\(self, )ctx: AppContext, (.*?\) -> None:\s*\n\s*super\(\)\.__init__\(.*?\)\s*\n)\s*self\.ctx = ctx\n'
    
    match = re.search(class_pattern, content, re.DOTALL)
    if match:
        old = match.group(0)
        # Reconstruct without ctx
        new = match.group(1) + match.group(2) + match.group(3)
        content = content.replace(old, new)
        print(f"  Fixed {filepath}:{cls_name} (removed ctx from __init__)")
    else:
        # Try simpler pattern - might have different super() args
        # Just replace the init line and remove self.ctx = ctx
        lines = content.split('\n')
        in_class = False
        class_indent = 0
        i = 0
        while i < len(lines):
            line = lines[i]
            stripped = line.lstrip()
            
            if stripped.startswith(f'class {cls_name}('):
                in_class = True
                class_indent = len(line) - len(stripped)
            elif in_class and stripped.startswith('class '):
                in_class = False
            
            if in_class and 'def __init__(self, ctx: AppContext,' in line:
                lines[i] = line.replace('ctx: AppContext, ', '')
                print(f"  Fixed {filepath}:{cls_name} __init__ signature")
            
            if in_class and stripped == 'self.ctx = ctx':
                lines.pop(i)
                print(f"  Fixed {filepath}:{cls_name} removed self.ctx = ctx")
                continue
            
            i += 1
        
        content = '\n'.join(lines)
    
    if content != original:
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(content)

# Also need to remove the unused AppContext import from files where it's no longer needed
# But these files still have main page classes that use it, so imports stay.

print("\nDone!")
