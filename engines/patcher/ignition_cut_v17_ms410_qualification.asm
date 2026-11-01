; V17: C3 means the limiter timing constraint is active.
; Native input/sensor faults veto it; event arbitration retains later native timing.
; Knock histories remain native-owned and are never cleared by qualification.
base 0x32F00
        push PSW
        push r4
        cmpb RH6,#0xC1
        jmpr cc_NE,done
        mov r4,0xFD30
        and r4,#0x007F
        jmpr cc_NE,done
        mov r4,0xFD32
        and r4,#0xC000
        jmpr cc_NE,done
        movb RH6,#0xC3
done:
        pop r4
        pop PSW
        rets
