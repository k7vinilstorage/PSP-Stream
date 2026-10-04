/* clips.bin (tools/h264_probe_clips.py) embutido no EBOOT. */
	.section .rodata
	.balign 64
	.global clips
clips:
	.incbin "clips.bin"
