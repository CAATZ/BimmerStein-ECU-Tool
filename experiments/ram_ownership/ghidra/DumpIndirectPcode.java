// Export decompiler pointer expressions for LOAD/STORE operations.
// args: <outPath> [minimumEntry]
import ghidra.app.decompiler.DecompInterface;
import ghidra.app.decompiler.DecompileResults;
import ghidra.app.script.GhidraScript;
import ghidra.program.model.listing.Function;
import ghidra.program.model.pcode.HighFunction;
import ghidra.program.model.pcode.HighVariable;
import ghidra.program.model.pcode.PcodeOp;
import ghidra.program.model.pcode.PcodeOpAST;
import ghidra.program.model.pcode.Varnode;
import java.io.FileWriter;
import java.io.PrintWriter;
import java.util.HashSet;
import java.util.Iterator;
import java.util.Set;

public class DumpIndirectPcode extends GhidraScript {
    private String expression(Varnode node, int depth, Set<Varnode> seen) {
        if (node == null) return "null";
        HighVariable high = node.getHigh();
        String type = high == null || high.getDataType() == null
            ? "?"
            : high.getDataType().getDisplayName();
        String leaf = String.format(
            "%s:%d:%s", node.getAddress(), node.getSize(), type
        );
        if (node.isConstant()) {
            return String.format("CONST(0x%x,%d)", node.getOffset(), node.getSize());
        }
        if (depth == 0 || !seen.add(node) || node.getDef() == null) {
            return "LEAF(" + leaf + ")";
        }
        PcodeOp definition = node.getDef();
        StringBuilder out = new StringBuilder(definition.getMnemonic()).append("(");
        for (int i = 0; i < definition.getNumInputs(); i++) {
            if (i != 0) out.append(",");
            out.append(expression(definition.getInput(i), depth - 1, seen));
        }
        return out.append(")").toString();
    }

    @Override
    public void run() throws Exception {
        String[] args = getScriptArgs();
        long minimumEntry = args.length > 1 ? Long.decode(args[1]) : 0;
        DecompInterface decompiler = new DecompInterface();
        decompiler.openProgram(currentProgram);
        int functions = 0;
        int expressions = 0;
        try (PrintWriter out = new PrintWriter(new FileWriter(getScriptArgs()[0]))) {
            out.println("entry\tfunction\tpc\top\texpression");
            for (Function function :
                    currentProgram.getFunctionManager().getFunctions(true)) {
                if (function.getEntryPoint().getOffset() < minimumEntry) continue;
                DecompileResults results =
                    decompiler.decompileFunction(function, 60, monitor);
                HighFunction high = results.getHighFunction();
                if (high == null) continue;
                functions++;
                Iterator<PcodeOpAST> operations = high.getPcodeOps();
                while (operations.hasNext()) {
                    PcodeOpAST operation = operations.next();
                    int opcode = operation.getOpcode();
                    if (opcode != PcodeOp.LOAD && opcode != PcodeOp.STORE) continue;
                    out.printf(
                        "%s\t%s\t%s\t%s\t%s%n",
                        function.getEntryPoint(),
                        function.getName(),
                        operation.getSeqnum().getTarget(),
                        operation.getMnemonic(),
                        expression(operation.getInput(1), 16, new HashSet<>())
                    );
                    expressions++;
                }
            }
        } finally {
            decompiler.dispose();
        }
        println(
            "DumpIndirectPcode: " + functions + " functions, "
            + expressions + " expressions"
        );
    }
}
