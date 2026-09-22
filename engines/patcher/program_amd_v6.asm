; program_amd_v6.asm — AMD/JEDEC word-program driver, drop-in replacement for the MS41.3
; Intel program driver at FILE/CPU 0x4230 (relocated to XRAM 0xE320, run from RAM).
; Keeps the original's entry/exit (push r9, save/restore DPP0, 2-byte local word, rets),
; the byte->word build from the source buffer [0xE73C]+index, the DPP0 page-crossing, and
; the index/length loop ([0xE73A]) VERBATIM.  Only the per-word command (Intel 0x40 -> AMD
; AA/55/A0) and the poll (SR.7 -> DQ6 toggle) change; VPP (P2.6) + settles removed.
; V6 stops at the first failed word, preserving status 2 for the host.
; Status: [0xE742]=1 ok / 2 err (whole block), [0xE741]+=2 progress (as original).  WORD writes
; for cmd/data (WE# strobe); byte reads for DQ6/DQ5.  target=[0xE656], unlock=DPP0 off 0xAAA/0x554.
base 0x4230
  mov [-r0],r9          ; push r9
  sub r0,#0x2           ; alloc 2-byte local (the data word) at [r0]
  mov r9,0xfe00         ; save DPP0
  mov r12,0xe656        ; r12 = target
  mov r14,#0x0          ; r14 = byte index
  jmpr cc_UC,FTEST
FBODY:
  ; ---- build data word: local.b0 = src[i], local.b1 = src[i+1] ----
  mov r4,r14
  movbz r4,RL4
  mov r5,0xe73c
  add r5,r4
  movb RL4,[r5]
  movb [r0],RL4
  mov r5,r14
  addb RL5,#0x1
  mov r14,r5
  movbz r4,RL5
  mov r5,0xe73c
  add r5,r4
  movb RL4,[r5]
  movb [r0+#0x1],RL4
  ; ---- v5 FF-transparent (read-modify-write) ----
  ; If a data byte is 0xFF, write back the CURRENT flash byte instead. A pad/prepend FF over
  ; an already-programmed byte (odd-block word0) then never demands an impossible 0->1, so the
  ; spurious DQ5 program-fail (which v4 recovers from via F0) is PREVENTED outright. Flash is in
  ; read-array here (the previous word auto-returns to read). FF over erased FF still writes FF
  ; = a true no-op. r4/r5 are scratch (the AMD unlock below reloads them); r12/[r0] preserved.
  mov r5,[r12]         ; current flash word at target: RL5 = low [r12], RH5 = high [r12+1]
  movb RL4,[r0]
  cmpb RL4,#0xff
  jmpr cc_NE,V5HI
  movb [r0],RL5        ; data low = FF -> keep current low byte (no 0->1)
V5HI:
  movb RL4,[r0+#0x1]
  cmpb RL4,#0xff
  jmpr cc_NE,V5DN
  movb [r0+#0x1],RH5   ; data high = FF -> keep current high byte (no 0->1)
V5DN:
  ; ---- AMD program: AA/55/A0 unlock, then data word -> target ----
  mov r5,#0x0AAA
  mov r4,#0x00AA
  mov [r5],r4          ; AA -> 0x555
  mov r5,#0x0554
  mov r4,#0x0055
  mov [r5],r4          ; 55 -> 0x2AA
  mov r5,#0x0AAA
  mov r4,#0x00A0
  mov [r5],r4          ; A0 -> 0x555  (program setup)
  mov [r12],[r0]       ; data word -> target [off]
  ; ---- poll DQ6 toggle + DQ5, LARGE iteration guard (match BSL monitor FPI) ----
  ; NOTE: a tight 0x97-tick T1 window (from the 28F200 Intel driver) is MARGINAL for
  ; AMD program timing -> words periodically time out + get skipped = FF holes. Use a
  ; big guard like the proven monitor; DQ6-done / DQ5-error exit early anyway.
  mov r13,#0x20        ; brief startup delay: let the program algorithm get underway
FDLY:                  ; (DQ6 toggling) before the first poll, so the leading-FF word of
  sub r13,#0x1         ; an odd-aligned block can't false-"done" and let the next word's
  jmpr cc_NE,FDLY      ; command race into a still-busy chip (the monitor's serial wait did this)
  mov r13,#0xffff      ; per-word poll guard
FPOLL:
  movb RL4,[r12]       ; DQ6 read1
  movb RL5,[r12]       ; DQ6 read2
  xorb RL4,RL5
  andb RL4,#0x40       ; DQ6
  jmpr cc_EQ,FPOK      ; stable -> programmed
  andb RL5,#0x20       ; DQ5
  jmpr cc_NE,FPERR     ; device error
  sub r13,#0x1
  jmpr cc_NE,FPOLL     ; guard not exhausted -> keep polling
FPERR:
  ; A DQ5 program-fail leaves the AMD chip OUT of read-mode; without a reset the
  ; NEXT word's command is swallowed (= the odd-block word1 FF-hole). The unavoidable
  ; case: an odd block's prepended-FF word0 writes FF over a byte the previous block
  ; already programmed (FF=1s over 0-bits is an impossible 0->1) -> spurious DQ5. The
  ; real data (high byte) already programmed before the verify tripped; just reset so
  ; word1 can program. Also makes GENUINE program failures recover instead of cascade.
  mov r5,#0x00f0
  mov [r12],r5         ; F0 -> AMD reset (clear program-fail state) before advancing
  movb RL4,#0x2
  movb 0xe742,RL4      ; preserve failure for the host
  jmpr cc_UC,FEXIT
FPOK:
  movb RL4,#0x1
  movb 0xe742,RL4      ; status = 1 (ok)
  movb RL5,#0x2
  addb 0xe741,RL5      ; progress += 2
FADV:
  ; ---- advance target word; DPP0++ on 16KB page crossing ----
  mov r4,#0x3ffe
  add r12,#0x2
  mov r5,r12
  cmp r5,r4
  jmpr cc_ULE,FNEXT
  add 0xfe00,#0x1      ; DPP0 -> next page
  mov r12,#0x0
FNEXT:
  mov r4,r14
  addb RL4,#0x1        ; index -> next byte (2nd of the word)
  mov r14,r4
FTEST:
  mov r4,r14
  cmpb RL4,0xe73a      ; index < length?
  jmpr cc_C,FBODY
FEXIT:
  ; ---- exit ----
  mov r5,#0x00f0
  mov [r12],r5         ; F0 -> AMD reset (read-array)
  mov 0xfe00,r9        ; restore DPP0
  add r0,#0x2          ; free local
  mov r9,[r0+]         ; pop r9
  rets
