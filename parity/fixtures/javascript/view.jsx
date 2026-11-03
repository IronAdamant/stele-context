export function View(props) {
    return greet(props.name);
}

export class Panel {
    render() {
        return View({ name: "ok" });
    }
}
