// Recover instruction streams at live file addresses that the saved listing
// classified as data. Use a read-only saved project or a disposable import;
// never save recovered streams into the production project.
// args: <outPath> <entryHex>...
import ghidra.app.script.GhidraScript;
import ghidra.program.model.address.Address;
import ghidra.program.model.listing.CodeUnit;
import ghidra.program.model.listing.Instruction;
import ghidra.program.model.listing.InstructionIterator;
import ghidra.program.model.symbol.Reference;
import java.io.BufferedWriter;
import java.io.FileWriter;
import java.io.PrintWriter;
import java.util.HashSet;
import java.util.Map;
import java.util.Set;
import java.util.TreeMap;

public class RecoverDisasm extends GhidraScript {
    @Override
    public void run() throws Exception {
        String[] args = getScriptArgs();
        Set<Long> original = new HashSet<>();
        Set<Long> requested = new HashSet<>();
        Map<Long, Instruction> recovered = new TreeMap<>();
        InstructionIterator before = currentProgram.getListing().getInstructions(true);
        while (before.hasNext()) {
            original.add(before.next().getAddress().getOffset());
        }

        for (int i = 1; i < args.length; i++) {
            Address entry = toAddr(Long.decode(args[i]));
            requested.add(entry.getOffset());
            CodeUnit unit =
                currentProgram.getListing().getCodeUnitContaining(entry);
            Address clearStart =
                unit == null ? entry : unit.getMinAddress();
            Address clearEnd = entry.add(0x100);
            clearListing(clearStart, clearEnd);
            disassemble(entry);
            InstructionIterator current =
                currentProgram.getListing().getInstructions(true);
            while (current.hasNext()) {
                Instruction instruction = current.next();
                long address = instruction.getAddress().getOffset();
                if (!original.contains(address) || requested.contains(address)) {
                    recovered.put(address, instruction);
                }
            }
        }

        try (PrintWriter out = new PrintWriter(
                new BufferedWriter(new FileWriter(args[0])))) {
            for (Instruction instruction : recovered.values()) {
                StringBuilder bytes = new StringBuilder();
                for (byte value : instruction.getBytes()) {
                    bytes.append(String.format("%02x", value));
                }
                out.printf(
                    "%06x: %-14s %-30s",
                    instruction.getAddress().getOffset(),
                    bytes,
                    instruction
                );
                for (Reference reference : instruction.getReferencesFrom()) {
                    if (reference.getReferenceType().isCall()
                            || reference.getReferenceType().isJump()) {
                        out.printf(
                            " -> %06x",
                            reference.getToAddress().getOffset()
                        );
                    }
                }
                out.println();
            }
        }
        println("RecoverDisasm: wrote " + recovered.size() + " instructions");
    }
}
