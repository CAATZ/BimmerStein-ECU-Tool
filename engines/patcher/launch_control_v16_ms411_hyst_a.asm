; launch_control V16 ms411: runtime RAM D801-D804.
; Derived from launch_control_v11_ms411_hyst_a.asm; only state operands are relocated.
; MS41.1 Launch V11: native fuel hysteresis A load.
; Return R13 exactly where native MOVBZ loaded it; native saturating subtraction follows.
; FF follows stock independently for each scalar. D801.3 selects stock for this episode.
; POP changes flags. Restore PSW last, then MOV of zero-extended R13 reproduces
; native MOVBZ E=0/N=0/Z while preserving the incoming C/V and all other PSW bits.
base 0x3FF80
        push PSW
        push DPP0
        push r4
        mov  DPP0,#4
        movbz r13,0x02DA
        jnb  0xFDB6.6,done
        movb RL4,0x3711
        cmpb RL4,#0
        jmpr cc_NE,done
        movb RL4,0xD801
        andb RL4,#0xF8
        cmpb RL4,#0xA0               ; valid marker and no stock-priority bit
        jmpr cc_NE,done
        movbz r4,0x371C
        cmp  r4,#0xFF
        jmpr cc_EQ,done
        mov  r13,r4
done:
        pop  r4
        pop  DPP0
        pop  PSW
        mov  r13,r13
        rets
