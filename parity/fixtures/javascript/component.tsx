export interface Props {
    title: string;
}

export function Card(props: Props) {
    return render(props.title);
}
