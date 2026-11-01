; MS41.2 Launch V12 shared actual timing qualification.
; State helper has already evaluated launch ownership, episode and live gates.
; Retain C1 episode when native protection vetoes the optional angle override.
; C3 qualifies combustion observation guards; C0/C1 do not suppress monitors.
; Pure qualification: RH6 input C0/C1, output C0/C1/C3. RL6 stays unchanged.
; No RAM publication: the serialized UPDATE owner publishes only after return.
; Critical input faults and either knock-sensor fault retain native timing.
; Native global and all six cylinder knock histories must be neutral.
base 0x3E740
        push PSW
        push r4
        push r5
        push r7
        cmpb RH6,#0xC1
        jmpr cc_NE,done
        mov r4,0xFD30
        and r4,#0x007F
        jmpr cc_NE,done
        mov r4,0xFD32
        and r4,#0xC000
        jmpr cc_NE,done
        movb RL4,0xE9D9
        cmpb RL4,#0x80
        jmpr cc_C,done
        mov r4,#0xE9CC
        mov r7,#6
knock_history:
        movb RL5,[r4+]
        cmpb RL5,#0x80
        jmpr cc_C,done
        sub r7,#1
        jmpr cc_NE,knock_history
        movb RH6,#0xC3
done:
        pop r7
        pop r5
        pop r4
        pop PSW
        rets
