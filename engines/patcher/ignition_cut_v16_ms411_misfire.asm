; ignition_cut V16 ms411: runtime RAM D801-D804.
; Derived from ignition_cut_v11_ms411_misfire.asm; only state operands are relocated.
; MS41.1 pending misfire recovery, in the free tail after native VANOS patch storage.
; Ignition Cut owns this helper even when Launch Control is absent.
base 0x3F89E
; Only native misfire owners consume bit 4; unknown startup RAM is not recovery.
misfire_pending:
        calls 0x3FDFA            ; combustion_active
        jmpr cc_NE,pending_done
        movb RL4,0xD801
        andb RL4,#0xF0
        cmpb RL4,#0xA0
        jmpr cc_NE,0x3F876
        movb RL4,0xD802
        andb RL4,#0x10
pending_done:
        rets
