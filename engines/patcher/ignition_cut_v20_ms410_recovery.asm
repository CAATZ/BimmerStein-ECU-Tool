; MS41.0 1429861 has two oxygen banks and no segment-adaptation learner.
base 0x33500
; This entry also serves the existing bank/electrical observation callers.
; D802.5 is an actual omitted charge, D801.2 is the Launch-owned fuel stage.
committed_cut:
        movb RL4,0xD801
        andb RL4,#0xF0
        cmpb RL4,#0xA0
        jmpr cc_NE,clear
        movb RL4,0xD802
        andb RL4,#0x20
        jmpr cc_NE,cut_observed
        movb RL4,0xD801
        andb RL4,#4
        jmpr cc_EQ,done
cut_observed:
        movb RL4,#3
        orb 0xD802,RL4
done:
        rets
clear:
        mov r4,#0
        rets

