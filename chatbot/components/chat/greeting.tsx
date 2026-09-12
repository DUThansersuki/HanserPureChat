export const Greeting = () => (
  <div
    className="flex w-full animate-[fade-in_0.2s_ease_both] flex-col items-start"
    key="overview"
  >
    <div className="mb-4 flex items-center gap-2 text-[12px] text-muted-foreground tracking-[0.18em]">
      <span aria-hidden="true" className="text-[var(--hanser-gold)]">
        ✦
      </span>
      <span>HANSER</span>
    </div>
    <div className="font-medium text-[32px] text-foreground leading-[1.4] tracking-[-0.02em] md:text-[36px]">
      随便聊聊啦 想说什么都可以~
    </div>
  </div>
);
