export const Greeting = () => (
  <div
    className="flex w-full animate-[fade-in_0.2s_ease_both] flex-col items-start"
    key="overview"
  >
    <div className="mb-4 flex items-center gap-2 text-[10px] text-muted-foreground tracking-[0.18em]">
      <span aria-hidden="true" className="text-[var(--hanser-gold)]">
        ✦
      </span>
      <span>HANSER</span>
    </div>
    <div className="font-medium text-[28px] text-foreground leading-[1.4] tracking-[-0.02em] md:text-[30px]">
      回来啦。
      <br />
      今天怎么样？
    </div>
    <div className="mt-3 text-[14px] text-muted-foreground leading-7">
      随便说点什么也可以。
    </div>
  </div>
);
