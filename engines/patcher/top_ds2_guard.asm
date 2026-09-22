; TOP-only resident DS2 guard, shared MS41.0/.1/.2/.3 boot worker.
; The descriptor redirects CPU 0x0F1E past the old program classifier to 0x0F74,
; then redirects dispatch CPU 0x0FD0 here. This reuses unreachable classifier
; bytes; it allocates neither a cave nor RAM and does not change the AMD driver.
; The descriptor also redirects only the application full-write table entry
; at file 4200 (CPU 0200), 073C -> 0E86, preserving listener/recovery/loader.
; Entry: RL6 = stock address class (3 calibration, 0 AIF/finalizer, 4 invalid).
; DPP0 and the user's r6-r9 are already saved by the stock worker.
; RL4 is the displaced load. Stock 0x0FD4 overwrites flags immediately.
; 0x1100 emits the existing B0 rejection and rejoins the stock DPP/stack restore.
; Reject before CPU 0x1052/0x1056/0x1060/0x106C EEPROM/E743/E740 side effects.
; 0F in class 0 is the stock finalizer (host uses address 0x1D07).
base 0x0F22
    movb RL4,0xe423
    cmpb RL6,#0x3
    jmpr cc_EQ,ALLOW
    cmpb RL4,#0x0f
    jmpr cc_NE,REJECT
    cmpb RL6,#0x0
    jmpr cc_NE,REJECT
ALLOW:
    jmpa cc_UC,0x0fd4
REJECT:
    jmpa cc_UC,0x1100
