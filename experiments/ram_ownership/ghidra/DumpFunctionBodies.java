// Read-only exporter for exact, potentially non-contiguous Ghidra function bodies.
import ghidra.app.script.GhidraScript;
import ghidra.program.model.address.AddressRange;
import ghidra.program.model.address.AddressRangeIterator;
import ghidra.program.model.listing.Function;
import java.io.FileWriter;
import java.io.PrintWriter;
import java.util.ArrayList;
import java.util.List;

public class DumpFunctionBodies extends GhidraScript {
    @Override
    public void run() throws Exception {
        String output = getScriptArgs()[0];
        try (PrintWriter writer = new PrintWriter(new FileWriter(output))) {
            writer.println("entry\tname\tbody_ranges");
            for (Function function : currentProgram.getFunctionManager().getFunctions(true)) {
                List<String> ranges = new ArrayList<>();
                AddressRangeIterator iterator = function.getBody().getAddressRanges();
                while (iterator.hasNext()) {
                    AddressRange range = iterator.next();
                    ranges.add(range.getMinAddress() + "-" + range.getMaxAddress());
                }
                writer.printf(
                    "%s\t%s\t%s%n",
                    function.getEntryPoint(),
                    function.getName(),
                    String.join(",", ranges)
                );
            }
        }
    }
}
