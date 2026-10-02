// AssembleC166.java - Ghidra headless postScript (Java; Jython is gone in GH12)
// Assemble a small C166 .asm (with labels) to raw bytes via the loaded c166
// Sleigh assembler. Two passes: resolve label addresses, then emit encodings.
//
//   ... -postScript AssembleC166.java <input.asm> <output.hex>
//
// .asm format:
//   base 0xFA40           ; load/base address (default 0xFA40)
//   LOOP:                 ; label (own line, or 'LABEL: instr')
//     mov S0TBUF,#0xA5    ; instructions; ';' or '#' begins a comment
//     jnb S0TIC.7,LOOP    ; label names in operands -> replaced by 0x<addr>
//     jmpr LOOP
//@category MS41
import ghidra.app.script.GhidraScript;
import ghidra.app.plugin.assembler.Assembler;
import ghidra.app.plugin.assembler.Assemblers;
import ghidra.program.model.address.Address;
import ghidra.program.model.address.AddressSpace;
import ghidra.program.model.mem.Memory;
import ghidra.program.model.symbol.SourceType;
import java.io.BufferedReader;
import java.io.ByteArrayOutputStream;
import java.io.FileReader;
import java.io.FileWriter;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.HashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

public class AssembleC166 extends GhidraScript {

    @Override
    public void run() throws Exception {
        String[] args = getScriptArgs();
        AddressSpace space = currentProgram.getAddressFactory().getDefaultAddressSpace();
        ensureSfrSymbols(space);
        if (args.length >= 3 && args[0].equals("--disasm")) {
            doDisasm(space, args[1], Long.decode(args[2]));
            return;
        }
        if (args.length >= 2 && args[0].equals("--probe")) {
            Assembler pasm = Assemblers.getAssembler(currentProgram);
            BufferedReader pb = new BufferedReader(new FileReader(args[1]));
            String pl;
            while ((pl = pb.readLine()) != null) {
                int cc = pl.indexOf(';'); if (cc >= 0) pl = pl.substring(0, cc);
                pl = pl.trim();
                if (pl.isEmpty()) continue;
                try {
                    byte[] b = pasm.assembleLine(space.getAddress(0xFA40L), pl);
                    StringBuilder bh = new StringBuilder();
                    for (byte x : b) bh.append(String.format("%02X", x & 0xFF));
                    println("PROBE OK   [" + pl + "] -> " + bh);
                } catch (Exception ex) {
                    String m = ex.getMessage();
                    if (m != null && m.length() > 90) m = m.substring(0, 90);
                    println("PROBE FAIL [" + pl + "] : " + m);
                }
            }
            pb.close();
            return;
        }
        if (args.length < 2) { println("ASM-ERROR: need <in.asm> <out.hex>"); return; }
        String inPath = args[0], outPath = args[1];

        Assembler asm = Assemblers.getAssembler(currentProgram);

        List<String> raw = new ArrayList<>();
        BufferedReader br = new BufferedReader(new FileReader(inPath));
        String line;
        while ((line = br.readLine()) != null) {
            String s = line;
            int c = s.indexOf(';'); if (c >= 0) s = s.substring(0, c);  // ';' only — '#' is the immediate prefix
            s = s.trim().replaceAll("\\s+", " ");  // collapse alignment whitespace
            if (!s.isEmpty()) raw.add(s);
        }
        br.close();

        long base = 0xFA40L;
        List<String[]> items = new ArrayList<>();
        Set<String> labelNames = new HashSet<>();
        Pattern labRe = Pattern.compile("^([A-Za-z_]\\w*):\\s*(.*)$");
        for (String s : raw) {
            if (s.toLowerCase().startsWith("base ")) {
                base = Long.decode(s.split("\\s+")[1]);
                continue;
            }
            Matcher m = labRe.matcher(s);
            if (m.matches()) {
                items.add(new String[]{"label", m.group(1)});
                labelNames.add(m.group(1));
                if (!m.group(2).trim().isEmpty())
                    items.add(new String[]{"instr", m.group(2).trim()});
            } else {
                items.add(new String[]{"instr", s});
            }
        }

        // pass 1: label addresses (instruction sizes are target-independent)
        Map<String, Long> labels = new HashMap<>();
        long cur = base;
        List<Object[]> placed = new ArrayList<>();
        for (String[] it : items) {
            if (it[0].equals("label")) {
                labels.put(it[1], cur);
            } else {
                // forward refs aren't known yet: use 'cur' (not base) as the placeholder
                // so relative jumps stay in range during sizing (size is target-independent)
                String txt = subLabels(it[1], labelNames, labels, cur);
                byte[] b;
                try {
                    b = asm.assembleLine(space.getAddress(cur), txt);
                } catch (Exception ex) {
                    println(String.format("PASS1-FAIL @0x%04X [%s] (orig [%s]): %s",
                            cur, txt, it[1], ex.getMessage()));
                    throw ex;
                }
                placed.add(new Object[]{cur, it[1]});
                cur += b.length;
            }
        }

        // pass 2: emit with resolved labels
        ByteArrayOutputStream out = new ByteArrayOutputStream();
        StringBuilder hex = new StringBuilder();
        for (Object[] p : placed) {
            long addr = (Long) p[0];
            String resolved = subLabels((String) p[1], labelNames, labels, base);
            byte[] b = asm.assembleLine(space.getAddress(addr), resolved);
            StringBuilder bh = new StringBuilder();
            for (byte x : b) bh.append(String.format("%02X", x & 0xFF));
            println(String.format("ASM 0x%04X  %-30s -> %s", addr, resolved, bh.toString()));
            out.write(b, 0, b.length);
            hex.append(bh);
        }

        FileWriter fw = new FileWriter(outPath);
        fw.write(hex.toString());
        fw.close();
        println(String.format("ASM-RESULT base=0x%X len=%d bytes=%s", base, out.size(), hex.toString()));
    }

    // (E)SFR names the assembler must resolve (addresses straight from c166cr.pspec)
    private static final String[][] SFRS = {
        {"DPP0", "0xFE00"}, {"DPP1", "0xFE02"}, {"DPP2", "0xFE04"}, {"DPP3", "0xFE06"},
        {"MDH", "0xFE0C"}, {"MDL", "0xFE0E"}, {"CP", "0xFE10"}, {"SP", "0xFE12"},
        {"STKOV", "0xFE14"}, {"STKUN", "0xFE16"},
        {"S0TBUF", "0xFEB0"}, {"S0RBUF", "0xFEB2"}, {"S0BG", "0xFEB4"},
        {"SYSCON", "0xFF12"}, {"S0TIC", "0xFF6C"}, {"S0RIC", "0xFF6E"},
        {"S0EIC", "0xFF70"}, {"S0CON", "0xFFB0"},
        {"P2", "0xFFC0"}, {"DP2", "0xFFC2"}, {"P3", "0xFFC4"}, {"DP3", "0xFFC6"},
    };

    // disassemble hex bytes at an address; print Ghidra's canonical instruction text
    private void doDisasm(AddressSpace space, String hex, long base) throws Exception {
        byte[] bytes = new byte[hex.length() / 2];
        for (int i = 0; i < bytes.length; i++)
            bytes[i] = (byte) Integer.parseInt(hex.substring(2 * i, 2 * i + 2), 16);
        Memory mem = currentProgram.getMemory();
        int tx = currentProgram.startTransaction("disasm");
        try {
            Address a = space.getAddress(base);
            if (!mem.contains(a))
                mem.createInitializedBlock("dis", a, bytes.length, (byte) 0, monitor, false);
            mem.setBytes(a, bytes);
            disassemble(a);
            ghidra.program.model.listing.Instruction ins = getInstructionAt(a);
            while (ins != null && ins.getAddress().getOffset() < base + bytes.length) {
                StringBuilder bh = new StringBuilder();
                for (byte x : ins.getBytes()) bh.append(String.format("%02X", x & 0xFF));
                println(String.format("DIS 0x%04X  %-10s -> %s",
                        ins.getAddress().getOffset(), bh.toString(), ins.toString()));
                ins = getInstructionAfter(ins);
            }
        } finally {
            currentProgram.endTransaction(tx, true);
        }
    }

    private void ensureSfrSymbols(AddressSpace space) throws Exception {
        Memory mem = currentProgram.getMemory();
        int tx = currentProgram.startTransaction("sfr-symbols");
        try {
            Address sfrBase = space.getAddress(0xFE00L);
            if (!mem.contains(sfrBase)) {
                mem.createUninitializedBlock("SFR", sfrBase, 0x200, false);
            }
            for (String[] s : SFRS) {
                Address a = space.getAddress(Long.decode(s[1]));
                if (getSymbolAt(a) == null) {
                    currentProgram.getSymbolTable().createLabel(a, s[0], SourceType.USER_DEFINED);
                }
            }
        } finally {
            currentProgram.endTransaction(tx, true);
        }
    }

    private String subLabels(String instr, Set<String> names, Map<String, Long> labels, long base) {
        String out = instr;
        for (String name : names) {
            long a = labels.containsKey(name) ? labels.get(name) : base;
            out = out.replaceAll("\\b" + Pattern.quote(name) + "\\b", String.format("0x%X", a));
        }
        return out;
    }
}
