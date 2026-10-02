; Ignition Cut V10 ms410 native transaction guards.
; E847 owns cut requests; E848 owns post-cut accepted-sample freshness.
; Every hook preserves the native R0 stack ABI and genuine fault fallback.
base 0x32D20

; R4 scratch; Z=0 means actual initialized cut. Caller saves R4.
; Each observed cut marks all fitted bank samples pending; no elapsed-time guess.
cut_active:
        movb RL4,0xE847
        andb RL4,#0xF0
        cmpb RL4,#0xA0
        jmpr cc_NE,cut_clear
        movb RL4,0xE847
        andb RL4,#0x07
        jmpr cc_EQ,cut_return
        movb RL4,#3
        movb 0xE848,RL4
cut_return:
        rets
cut_clear:
        mov r4,#0
        rets

; R5 is the selected native bank pointer. Only its front pending bit blocks.
; Branch on the result before POP, which changes C166 N/Z flags.
front_block:
        calls cut_active
        jmpr cc_NE,front_block_done
        movb RL4,0xE847
        andb RL4,#0xF0
        cmpb RL4,#0xA0
        jmpr cc_NE,cut_clear
        movb RL4,0xE848
        cmp r5,#0xED56
        jmpr cc_NE,front_block_bank2
        andb RL4,#1
        jmpr cc_UC,front_block_done
front_block_bank2:
        andb RL4,#2
front_block_done:
        rets

; Native argument R14 is the bank; R12 is the signed-step argument.
; Replay 88D08880 on the R0 software stack, never hardware PUSH R13/R8.
stft:
        push r4
        push r5
        mov r5,r14
        calls front_block
        jmpr cc_EQ,stft_restore
        pop r5
        pop r4
        rets
stft_restore:
        pop r5
        pop r4
stft_stock:
        mov [-r0],r13
        mov [-r0],r8
        jmps 0x28668

; Guard the complete qualification and paired-transfer transaction.
; FD30.1 retains the native genuine-fault reset of both adaptation terms.
learning:
        jb 0xFD30.1,learning_stock
        push r4
        push r5
        mov r5,r12
        calls front_block
        jmpr cc_EQ,learning_restore
        pop r5
        pop r4
        rets
learning_restore:
        pop r5
        pop r4
learning_stock:
        mov [-r0],r6
        mov [-r0],r8
        jmps 0x28956

; Independent native STFT neutralizer: freeze before either R0 save.
alternate_stft:
        push r4
        push r5
        mov r5,r12
        calls front_block
        jmpr cc_EQ,alternate_stft_restore
        pop r5
        pop r4
        rets
alternate_stft_restore:
        pop r5
        pop r4
alternate_stft_stock:
        mov [-r0],r6
        mov [-r0],r9
        jmps 0x2848A

; Freeze the entire 202/203 evaluator, retaining previous records/counters.
mixture:
        push r4
        push r5
        mov r5,r12
        calls front_block
        jmpr cc_EQ,mixture_restore
        pop r5
        pop r4
        rets
mixture_restore:
        pop r5
        pop r4
mixture_stock:
        mov [-r0],r6
        mov [-r0],r9
        jmps 0x2880C

coil:
        push r4
        calls cut_active
        jmpr cc_EQ,coil_stock
        pop r4
        jmps 0x2CF58
coil_stock:
        pop r4
        movb RL4,0xFADE
        jmps 0x2CC9C

lambda:
        jb 0xFD30.1,lambda_native
        push r4
        push r5
        mov r5,r9
        calls front_block
        jmpr cc_EQ,lambda_stock
        pop r5
        pop r4
        jmps 0x27DFA
lambda_stock:
        pop r5
        pop r4
lambda_native:
        movb RL4,[r9+#0x33]
        jmps 0x27D46

o2_front:
        push r4
        calls cut_active
        jmpr cc_EQ,o2_front_stock
        pop r4
        jmps 0x279C0
o2_front_stock:
        pop r4
        mov r4,#0xED90
        jmps 0x27988

; O2 acquisition/electrical cleanup precedes this hook.
; Freeze combustion history, envelope and learning together; retain bank telemetry
; and the original R0 epilogue after restoring the saved bank flags.
observation:
        jb 0xFD30.1,observation_native
        push r4
        push r5
        mov r5,r9
        calls front_block
        jmpr cc_EQ,observation_stock
        mov r4,[r9+#2]
        mov 0xFD0E,r4
        mov r4,[r9+#4]
        mov 0xFD10,r4
        pop r5
        pop r4
        jmps 0x27BBE
observation_stock:
        pop r5
        pop r4
observation_native:
        jb 0xFD14.0,observation_taken
        jmps 0x27A5C
observation_taken:
        jmps 0x27A9A
