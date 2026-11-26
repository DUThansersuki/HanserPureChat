import Image from "next/image";

export const Greeting = () => (
  <div
    className="flex w-full flex-col items-start gap-3"
    key="overview"
  >
    <Image alt="小憨同学" className="welcome-avatar size-13 rounded-full object-cover" height={52} src="/images/hanser-avatar.jpg" unoptimized width={52} />
    <h1 className="welcome-title text-[27px] font-semibold leading-[1.35] text-foreground">来啦，聊点什么？</h1>
    <p className="welcome-description text-[14px] text-muted-foreground">可以从一件小事说起。</p>
  </div>
);
