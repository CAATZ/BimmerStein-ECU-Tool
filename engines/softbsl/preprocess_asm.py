#!/usr/bin/env python3
"""preprocess_asm.py <in.asm> <out.asm> — make a hand-written C166 .asm ready for
Ghidra AssembleC166.java. Transforms (discovered via headless --probe):
  * trailing-h hex (0E000h, 09Ch)        -> 0x notation (0xE000, 0x9C)
  * EQU defs (BUF EQU 0E000h)            -> inlined + the def line dropped
  * uppercase mnemonics (MOV/CMPB/JMPR)  -> lowercase (operands/regs/cc untouched)
  * CALL/call -> calls ; RET/ret -> rets (incl. after 'label:'); keeps existing calls/rets
  * char literals (#'I')                 -> #0xNN
  * #077 (octal-looking)                 -> #77
NOTE: mem-direct *immediate* forms (movb/cmpb/mov mem,#imm to a non-reg address)
  are NOT auto-fixable here — they must be rewritten in the source to use a scratch reg.
"""
import re, sys


def preprocess_text(source):
    """Return ``(normalized_source, equ_values)`` without filesystem side effects."""
    src = str(source).split("\n")
    equ = {}
    for ln in src:
        code = ln.split(";", 1)[0]
        m = re.match(r"\s*(\w+)\s+EQU\s+(\S+)", code, re.I)
        if m:
            v = m.group(2); mh = re.match(r"([0-9][0-9A-Fa-f]*)h$", v)
            equ[m.group(1)] = ("0x%X" % int(mh.group(1), 16)) if mh else v

    def conv_h(s):
        return re.sub(r"\b([0-9][0-9A-Fa-f]*)h\b",
                      lambda m: "0x%X" % int(m.group(1), 16), s)

    def proc(instr):
        t = instr.split(None, 1); mne = t[0]; rest = t[1] if len(t) > 1 else ""
        ml = mne.lower()
        if ml == "call": ml = "calls"
        elif ml == "ret": ml = "rets"
        for k, v in equ.items():
            rest = re.sub(r"\b" + re.escape(k) + r"\b", v, rest)
        rest = rest.replace("#077", "#77")
        rest = re.sub(r"#'(.)'", lambda m: "#0x%02X" % ord(m.group(1)), rest)
        rest = conv_h(rest)
        return ml + ((" " + rest) if rest else "")

    out = []
    for ln in src:
        code, sep, comment = ln.partition(";")
        if re.match(r"^\s*\w+\s+EQU\s+", code, re.I):
            continue
        if code.strip():
            m = re.match(r"^(\s*)([A-Za-z_]\w*:)(\s*)(.*)$", code)
            if m:
                indent, label, sp, instr = m.groups()
            else:
                m2 = re.match(r"^(\s*)(.*)$", code)
                indent, label, instr = m2.group(1), "", m2.group(2)
            io = proc(instr) if instr.strip() else ""
            code = (indent + label + (" " + io if io else "")) if label else (indent + io)
        out.append(code + ((";" + comment) if sep else ""))

    return "\n".join(out), equ


def main(inp, outp):
    with open(inp, encoding="utf-8") as handle:
        txt, equ = preprocess_text(handle.read())
    with open(outp, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(txt)
    nocom = re.sub(r";.*", "", txt)
    print("wrote %s (%d lines); EQU=%s" % (outp, len(txt.splitlines()), equ))
    print("  leftover trailing-h:", re.findall(r"\b[0-9][0-9A-Fa-f]*h\b", nocom)[:8])
    print("  leftover CALL/RET/char:", re.findall(r"(?mi)\b(?:CALL|RET)\b|#'.'", nocom)[:8])

if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
