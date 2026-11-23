"""
Turns a Cpp2IL ISIL dump into a Ghidra symbol script.

For an ARM64 IL2CPP build there is no route back to C#: IL recovery reconstructs nothing on
ARM, and the dumpers that feed Ghidra (Il2CppDumper, Il2CppInspector) cannot read metadata
past roughly v31. What Cpp2IL can still do is dump every method with its address, and that is
enough to name the functions in Ghidra.

With the names applied, Ghidra's decompiler shows the game's own calls by name instead of
`FUN_02ce7a68`, which is the difference between unreadable assembly and followable logic.

Produces, in the chosen folder:
  il2cpp_methods.csv              address, type, method - a plain lookup table
  ghidra_name_il2cpp_methods.py   run inside Ghidra to apply every name
"""

import os
import re

METHOD_RE = re.compile(r"^Method:\s*(.+?)\s*$")
TYPE_RE = re.compile(r"^Type:\s*(.+?)\s*$")
ADDRESS_RE = re.compile(r"^\s*(0x[0-9A-Fa-f]+)\s")


def parse_isil_dump(isil_root):
    """Yields (address, type_name, method_signature) for every method in the dump."""
    for folder, _dirs, files in os.walk(isil_root):
        for name in files:
            if not name.endswith(".txt"):
                continue
            path = os.path.join(folder, name)
            try:
                lines = open(path, encoding="utf-8", errors="replace").read().splitlines()
            except OSError:
                continue

            type_name = ""
            method = None
            awaiting_address = False
            for line in lines:
                found_type = TYPE_RE.match(line)
                if found_type:
                    type_name = found_type.group(1)
                    continue
                found_method = METHOD_RE.match(line)
                if found_method:
                    method = found_method.group(1)
                    awaiting_address = False
                    continue
                if line.strip() == "Disassembly:":
                    awaiting_address = True
                    continue
                if awaiting_address and method:
                    found_address = ADDRESS_RE.match(line)
                    if found_address:
                        yield found_address.group(1), type_name, method
                        method = None
                        awaiting_address = False


def _safe_name(type_name, method):
    """A Ghidra-friendly symbol: Namespace_Type__MethodName."""
    signature = method.split("(")[0].strip()
    short_method = signature.split(" ")[-1] if " " in signature else signature
    combined = f"{type_name}__{short_method}"
    return re.sub(r"[^0-9A-Za-z_]", "_", combined)[:180] or "il2cpp_method"


def build(isil_root, out_dir):
    """Writes the lookup table and the Ghidra script. Returns how many methods were found."""
    os.makedirs(out_dir, exist_ok=True)
    entries = list(parse_isil_dump(isil_root))
    if not entries:
        return 0

    csv_path = os.path.join(out_dir, "il2cpp_methods.csv")
    with open(csv_path, "w", encoding="utf-8", newline="") as handle:
        handle.write("address,type,method\n")
        for address, type_name, method in entries:
            clean = method.replace('"', "'")
            handle.write(f'{address},"{type_name}","{clean}"\n')

    script_path = os.path.join(out_dir, "ghidra_name_il2cpp_methods.py")
    with open(script_path, "w", encoding="utf-8", newline="") as handle:
        handle.write('''# Ghidra script: names every IL2CPP method recovered from the ISIL dump.
#
# How to use:
#   1. In Ghidra, import the game's libil2cpp.so and let the initial analysis finish.
#   2. Window > Script Manager > the "Run Script" toolbar button, and pick this file.
#   3. Open any named function; the decompiler now shows real call names.
#
# Addresses come from Cpp2IL and are relative to the image base of libil2cpp.so. If the names
# land in the wrong place, set BASE_OFFSET below to the difference between Ghidra's image base
# and 0, then run it again.

BASE_OFFSET = 0

METHODS = [
''')
        for address, type_name, method in entries:
            handle.write(f'    ({address}, "{_safe_name(type_name, method)}"),\\n'.replace("\\n", "\n"))
        handle.write(''']

def apply_names():
    space = currentProgram.getAddressFactory().getDefaultAddressSpace()
    listing = currentProgram.getListing()
    function_manager = currentProgram.getFunctionManager()
    base = currentProgram.getImageBase().getOffset()
    named = 0
    created = 0
    for offset, name in METHODS:
        try:
            address = space.getAddress(base + offset + BASE_OFFSET)
            function = function_manager.getFunctionAt(address)
            if function is None:
                function = createFunction(address, name)
                if function is not None:
                    created += 1
            if function is not None:
                function.setName(name, ghidra.program.model.symbol.SourceType.USER_DEFINED)
                named += 1
            else:
                createLabel(address, name, True)
        except Exception:
            continue
    print("IL2CPP naming finished: %d named, %d functions created." % (named, created))

apply_names()
''')
    return len(entries)
