pub struct Box {
    n: i32,
}

pub fn answer() -> i32 {
    42
}

impl Box {
    pub fn value(&self) -> i32 {
        self.n
    }
}
