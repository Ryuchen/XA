export function SkipLink() {
  return (
    <a
      href="#main-content"
      className="skip-link"
      onClick={(event) => {
        // HashRouter owns location.hash; moving focus must not change the route.
        event.preventDefault()
        const main = document.getElementById("main-content")
        if (!main) return
        main.focus({ preventScroll: true })
        main.scrollIntoView({ block: "start" })
      }}
    >
      跳到主内容
    </a>
  )
}
