; erase_amd_v2.asm — AMD/JEDEC sector-erase driver for the MS41 family.
; Replaces the driver at file 0x432E / CPU 0x032E, copied to XRAM 0xE320.
; V2 rechecks DQ6 when DQ5 is high before reporting a device failure.
; Same entry (offset 0) and exit (rets), same status vars [0xE742]/[0xE744], same
; watchdog-serviced timeout loop — only the command set (Intel->AMD) and poll
; (SR.7 -> DQ6 toggle) change, and the VPP gate (P2.6) + 19-tick settle are removed.
; Register use = the original's clobber set only (r4,r5,r12,r13,r14); r0(user-SP)/r9/etc.
; untouched. Command/data writes are WORD (mov [Rw],Rw) so WE# strobes; DQ reads are byte.
; Position-independent: only relative jmpr + absolute SFR/SRAM data refs.  target = [0xE656].
base 0x432E
  mov r12,0xfe52        ; r12 = T1 snapshot (coarse timeout timing)
  mov r14,#0x0          ; r14 = coarse timeout counter
  ; ---- AMD sector-erase unlock (AA/55/80/AA/55/30); no VPP, no settle ----
  mov r13,#0x0AAA
  mov r5,#0x00AA
  mov [r13],r5          ; AA -> word 0x555
  mov r13,#0x0554
  mov r5,#0x0055
  mov [r13],r5          ; 55 -> word 0x2AA
  mov r13,#0x0AAA
  mov r5,#0x0080
  mov [r13],r5          ; 80 -> word 0x555  (erase command)
  mov r5,#0x00AA
  mov [r13],r5          ; AA -> word 0x555  (r13 still 0xAAA)
  mov r13,#0x0554
  mov r5,#0x0055
  mov [r13],r5          ; 55 -> word 0x2AA
  mov r4,0xe656
  mov r5,#0x0030
  mov [r4],r5           ; 30 -> sector [target]  (erase confirm)
  ; ---- short delay so the internal erase has begun before first DQ6 read ----
  mov r5,#0x0800
EDLY:
  sub r5,#0x1
  jmpr cc_NE,EDLY
  ; ---- poll: DQ6 toggle + DQ5, watchdog-serviced, [0xE744] coarse timeout ----
POLL:
  mov 0xffae,#0x101     ; WDTCON reload
  srvwdt                ; service watchdog (sector erase ~seconds)
  mov r5,0xfe52
  sub r5,r12
  cmp r5,#0x754         ; 1876 T1 ticks elapsed?
  jmpr cc_C,RDQ6
  add r14,#0x1          ; tick the coarse counter
  mov r12,0xfe52
RDQ6:
  mov r4,0xe656         ; r4 = target pointer
  movb RL5,[r4]         ; DQ6 read1
  movb RH5,[r4]         ; DQ6 read2
  xorb RL5,RH5          ; read1 ^ read2
  andb RL5,#0x40        ; isolate DQ6
  jmpr cc_EQ,EOK        ; DQ6 stable -> erase complete
  andb RH5,#0x20        ; DQ5 of read2 (timing-limit exceeded)
  jmpr cc_EQ,EWAIT
  ; Completion can turn read2 into array data with bit5 set. Recheck DQ6.
  movb RL5,[r4]
  movb RH5,[r4]
  xorb RL5,RH5
  andb RL5,#0x40
  jmpr cc_EQ,EOK
  jmpr cc_UC,EFAIL      ; still toggling after DQ5 -> device error
EWAIT:
  cmp r14,0xe744        ; coarse timeout reached?
  jmpr cc_C,POLL        ; r14 < [0xE744] -> keep polling
EFAIL:
  movb RL5,#0x2
  movb 0xe742,RL5       ; status = 2 (fail)  [host _decode_sr]
  jmpr cc_UC,ERST
EOK:
  movb RL5,#0x1
  movb 0xe742,RL5       ; status = 1 (success)
ERST:
  mov r4,0xe656
  mov r5,#0x00f0
  mov [r4],r5           ; F0 -> AMD reset (back to read-array)
  rets
